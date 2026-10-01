"""Plain-code normalisation. No model calls here: every number a buyer relies on is computed in this file."""
import re

THRESH = 0.8
CUR_MAP = {"INR": "INR", "RS": "INR", "RS.": "INR", "₹": "INR", "RUPEES": "INR", "USD": "USD", "$": "USD", "US$": "USD"}


def canon_currency(c):
    if not c:
        return "INR"
    k = c.strip().upper().replace(" ", "")
    return CUR_MAP.get(k, k)


def rfx_unit_class(u):
    u = (u or "").lower()
    if "kg" in u:
        return "kg"
    if "bundle" in u:
        return "bundle"
    return "each"


def parse_unit(u):
    """Return (class, multiplier). 'per 100 pcs' -> ('each', 100)."""
    if not u:
        return None, 1
    s = u.lower()
    if "kg" in s:
        return "kg", 1
    if "bundle" in s or "bndl" in s:
        return "bundle", 1
    if re.search(r"\b(pc|pcs|piece|pieces|nos|no|box|boxes|unit|units|each|set|sets|ea)\b", s) or "100" in s:
        m = re.search(r"(\d+)\s*(?:pcs?|pieces?|nos|boxes|units|sets?)", s)
        return "each", (int(m.group(1)) if m else 1)
    return "?", 1


def _clip(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def _flag(vendor, item_id, ftype, severity, msg, resolutions, key_suffix=""):
    key = f"{vendor}|{item_id}|{ftype}{key_suffix}"
    res = resolutions.get(key)
    return dict(type=ftype, severity=severity, message=msg, key=key, vendor=vendor, item_id=item_id,
                resolved=res is not None, resolution=res)


def vendor_eligibility(doc):
    q = doc["questionnaire"]
    if q["iso_9001"] == "Yes" and q["burst_report"] == "Yes":
        return True, "ISO 9001 and burst report both provided"
    reasons = []
    if q["iso_9001"] != "Yes":
        reasons.append(f"ISO 9001: {q['iso_9001']}")
    if q["burst_report"] != "Yes":
        reasons.append(f"burst test report: {q['burst_report']}")
    return False, "; ".join(reasons)


def vendor_flags(v, doc, resolutions):
    out = []
    for d in doc.get("discounts", []):
        out.append(_flag(v, 0, "DISCOUNT_FOUND", "decision",
                         f"{d['percent']}% discount found ({d['source_ref']}): \"{_clip(d['source_snippet'], 300)}\". Not applied until you accept it.", resolutions))
        break
    for j, l in enumerate(doc["lines"]):
        if l.get("rfx_item_id") is None:
            price = f"{l.get('currency') or ''} {l['price']:g} {l.get('price_unit') or ''}".strip() if l.get("price") is not None else "an unreadable price"
            out.append(_flag(v, 0, "UNMATCHED_LINE", "decision",
                             f"Vendor quoted \"{l['vendor_description']}\" at {price} ({l['source_ref']}), but it was not matched to an RFx line. "
                             "Pick the line it belongs to, or leave it out.", resolutions, key_suffix=f"#{j}"))
    t = doc.get("terms", {})
    if t.get("freight") and not t.get("freight_included") and not t.get("freight_amount_stated"):
        out.append(_flag(v, 0, "AMBIGUOUS_TERM", "decision",
                         f"Freight is extra but no amount is given (\"{_clip(t['freight'], 300)}\"). Landed cost is not known.", resolutions))
    elif t.get("freight") is None:
        out.append(_flag(v, 0, "AMBIGUOUS_TERM", "decision", "Freight terms not stated.", resolutions))
    return out


def build_cells(docs, items, fx, last_year, resolutions):
    """docs: {vendor_name: Doc dict}. Returns cells[vendor][item_id] and vendor_flags[vendor]."""
    cells, vflags = {}, {}
    for v, doc in docs.items():
        vflags[v] = vendor_flags(v, doc, resolutions)
        doc = _apply_mappings(v, doc, resolutions)
        disc = doc.get("discounts") or []
        disc_pct = disc[0]["percent"] if disc else 0
        disc_ok = any(f["type"] == "DISCOUNT_FOUND" and f["resolved"] for f in vflags[v])
        cells[v] = {}
        for it in items:
            cells[v][it["id"]] = _cell(v, doc, it, fx, last_year, resolutions, disc_pct if disc_ok else 0)
    return cells, vflags


def _apply_mappings(v, doc, resolutions):
    """Buyer-chosen RFx line for lines the model could not match (UNMATCHED_LINE resolved with action 'map')."""
    lines = []
    for j, l in enumerate(doc["lines"]):
        r = resolutions.get(f"{v}|0|UNMATCHED_LINE#{j}")
        if l.get("rfx_item_id") is None and r and r.get("action") == "map":
            l = {**l, "rfx_item_id": r["item_id"], "notes": ((l.get("notes") or "") + " Matched by buyer.").strip()}
        lines.append(l)
    return {**doc, "lines": lines}


def _cell(v, doc, it, fx, last_year, resolutions, disc_pct):
    i = it["id"]
    cell = dict(vendor=v, item_id=i, flags=[], price=None, display_price=None, scenario_price=None,
                status="not_quoted", source=None, raw=None, receipt="", gross=None)
    matched = [l for l in doc["lines"] if l.get("rfx_item_id") == i]
    lines = [l for l in matched if l.get("price") is not None]
    if matched and not lines:  # vendor quoted the line but the price could not be read
        l = max(matched, key=lambda x: x.get("confidence", 0))
        cell["source"] = dict(ref=l["source_ref"], snippet=l["source_snippet"], box=l.get("box_2d"), desc=l["vendor_description"])
        f = _flag(v, i, "UNREADABLE_PRICE", "blocking",
                  "Vendor quoted this line but the price could not be read. Check the source and enter the value.", resolutions)
        cell["flags"] = [f]
        cell["status"] = "blocked"
        cell["unreadable"] = True
        cell["receipt"] = "Price unreadable in the source"
        if f["resolved"] and f["resolution"].get("action") == "edit":
            value = float(f["resolution"]["price"])
            cell["gross"] = value
            cell["price"] = cell["display_price"] = value * (1 - disc_pct / 100)
            cell["status"] = "ok"
            cell["receipt"] = f"Price unreadable in the source | Buyer-entered value Rs {value:g}"
        return cell
    if not lines:
        ly = last_year.get(i)
        if doc.get("baseline_reference") and ly is not None:
            f = _flag(v, i, "BASELINE_ASSUMED", "blocking",
                      f"Vendor said some rates are 'same as last year'. Last year's rate is Rs {ly:g}. Confirm before using.", resolutions)
            cell["flags"].append(f)
            cell["display_price"] = ly
            cell["status"] = "assumed"
            cell["receipt"] = f"Assumed from last year's rate Rs {ly:g}"
            cell["source"] = dict(ref="last-year file", snippet=doc.get("baseline_text") or "same as last year", file=None, box=None)
            if f["resolved"]:
                cell["price"] = ly * (1 - disc_pct / 100)
                cell["status"] = "ok"
        else:
            cell["flags"].append(_flag(v, i, "MISSING_ITEM", "info", "Vendor did not quote this line.", resolutions))
        return cell

    l = max(lines, key=lambda x: x.get("confidence", 0))
    cell["source"] = dict(ref=l["source_ref"], snippet=l["source_snippet"], box=l.get("box_2d"), desc=l["vendor_description"])
    cur = canon_currency(l.get("currency"))
    cell["raw"] = f"{l.get('currency') or ''} {l['price']:g} {l.get('price_unit') or '(unit not stated)'}".strip()
    value = float(l["price"])
    receipts = [f"As quoted: {cell['raw']}"]
    flags = []

    if cur == "USD":
        value *= fx
        receipts.append(f"USD {l['price']:g} x {fx:g} = Rs {value:.2f}")
        flags.append(_flag(v, i, "CURRENCY_CONVERTED", "info", f"Quoted in USD, converted at {fx:g}.", resolutions))
    elif cur != "INR":
        value = None
        flags.append(_flag(v, i, "CURRENCY_UNKNOWN", "blocking", f"Currency '{cur}' cannot be converted.", resolutions))

    ucls, mult = parse_unit(l.get("price_unit"))
    rcls = rfx_unit_class(it["unit"])
    if ucls is None or ucls != rcls or mult != 1:
        f = _flag(v, i, "UNIT_MISMATCH", "blocking",
                  f"Quoted '{l.get('price_unit') or 'no unit'}' but the RFx unit is '{it['unit']}'. Confirm 1 {it['unit']} = 1 unit quoted"
                  + (f" ({mult} pcs per price)" if mult != 1 else "") + ".", resolutions)
        flags.append(f)
        if value is not None and ucls == rcls and mult > 1:
            value = value / mult
            receipts.append(f"per {mult} -> per 1: / {mult}")
        elif ucls != rcls:
            value = None if not f["resolved"] else value

    if l.get("confidence", 1) < THRESH:
        flags.append(_flag(v, i, "LOW_CONFIDENCE", "blocking",
                           f"Reading confidence {l.get('confidence', 0):.0%}. Check the source before relying on it.", resolutions))
    if l.get("applicability_uncertain"):
        flags.append(_flag(v, i, "AMBIGUOUS_APPLICABILITY", "blocking",
                           "The vendor's rate could apply to more than one line. Confirm it applies here.", resolutions))

    edit = next((f["resolution"] for f in flags if f["resolved"] and f["resolution"].get("action") == "edit"), None)
    if edit:
        value = float(edit["price"])
        receipts.append(f"Buyer-entered value Rs {value:g}")

    cell["gross"] = value
    if value is not None and disc_pct:
        value = value * (1 - disc_pct / 100)
        receipts.append(f"less {disc_pct:g}% discount = Rs {value:.2f}")
    cell["flags"] = flags
    cell["display_price"] = value
    cell["receipt"] = " | ".join(receipts)
    open_block = [f for f in flags if f["severity"] == "blocking" and not f["resolved"]]
    if value is not None and not open_block:
        cell["price"] = value
        cell["status"] = "converted" if any(f["type"] == "CURRENCY_CONVERTED" for f in flags) else "ok"
    else:
        cell["status"] = "blocked"
        only_lowconf = open_block and all(f["type"] == "LOW_CONFIDENCE" for f in open_block)
        if only_lowconf and value is not None:
            cell["scenario_price"] = value
    return cell


def summarise(docs, items, cells, vflags):
    qty = {it["id"]: it["qty"] for it in items}
    rows, total_cells, ready = [], 0, 0
    for v, doc in docs.items():
        el, why = vendor_eligibility(doc)
        usable = [c for c in cells[v].values() if c["price"] is not None]
        quoted = [c for c in cells[v].values() if c["status"] != "not_quoted" and not c["receipt"].startswith("Assumed")]
        assumed = [c for c in cells[v].values() if c["receipt"].startswith("Assumed")]
        open_f = [f for c in cells[v].values() for f in c["flags"] if f["severity"] == "blocking" and not f["resolved"]]
        open_f += [f for f in vflags[v] if not f["resolved"]]
        total = sum(qty[c["item_id"]] * c["price"] for c in usable)
        rows.append(dict(vendor=v, eligible=el, eligibility_note=why, lines_quoted=len(quoted), lines_assumed_from_last_year=len(assumed),
                         lines_usable=len(usable),
                         total=total, open_flags=len(open_f)))
        for c in cells[v].values():
            total_cells += 1
            if c["status"] in ("ok", "converted", "not_quoted"):
                ready += 1
    return rows, (ready / total_cells if total_cells else 0)


def all_open_flags(cells, vflags):
    out = []
    for v in vflags:
        out += [f for f in vflags[v] if not f["resolved"]]
        for c in cells[v].values():
            out += [f for f in c["flags"] if f["severity"] == "blocking" and not f["resolved"]]
    return out
