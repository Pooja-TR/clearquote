"""Follow-up emails: every open question about a vendor's quote becomes one numbered point in a draft to that vendor.
Plain code from the flags; no model call. A point disappears from the draft once the buyer settles it."""
import datetime as dt


def item_ranges(ids):
    """[1, 2, 3, 5, 7, 8] -> '1-3, 5, 7-8'."""
    ids, out, start = sorted(ids), [], None
    for k, i in enumerate(ids):
        if start is None:
            start = i
        if k + 1 == len(ids) or ids[k + 1] != i + 1:
            out.append(f"{start}" if start == i else f"{start}-{i}")
            start = None
    return ", ".join(out)


def _rs(x):
    return f"Rs {x:,.2f}"


def quoted(c):
    """How the vendor quoted it, in plain words: 'INR 3100 100 Pcs' -> 'Rs 3,100 per 100 Pcs'."""
    raw = (c.get("raw") or "").split()
    if len(raw) < 2:
        return c.get("raw") or ""
    cur, rest = raw[0], raw[1:]
    try:
        amount = f"{float(rest[0]):,.2f}".rstrip("0").rstrip(".")
        rest = rest[1:]
    except ValueError:
        return c["raw"]
    money = f"{'Rs' if cur.upper() in ('INR', 'RS', 'RS.', '₹') else cur} {amount}"
    unit = " ".join(rest).lstrip("/").strip()
    return f"{money} per {unit[4:] if unit.lower().startswith('per ') else unit}" if unit else money


def questions(v, doc, items, cells, vflags):
    """Numbered questions for one vendor, from its unresolved flags, unquoted items and missing quality documents."""
    desc = {it["id"]: it["desc"] for it in items}
    unit = {it["id"]: it["unit"] for it in items}
    open_cell = {}
    for i, c in cells[v].items():
        for f in c["flags"]:
            if f["severity"] == "blocking" and not f["resolved"]:
                open_cell.setdefault(f["type"], []).append(i)
    vf = {}
    for f in vflags[v]:
        if not f["resolved"]:
            vf.setdefault(f["type"], []).append(f)
    q = []

    for i in open_cell.get("UNREADABLE_PRICE", []):
        q.append(f"Item {i} ({desc[i]}): the price on your rate card is not legible. Please confirm the price per {unit[i]}.")
    by_unit = {}
    for i in open_cell.get("UNIT_MISMATCH", []):
        by_unit.setdefault(quoted(cells[v][i]).split(" per ", 1)[-1], []).append(i)
    for vendor_unit, ids in by_unit.items():
        u = unit[ids[0]]
        reads = "; ".join(f"item {i} {quoted(cells[v][i])} = {_rs(cells[v][i]['display_price'])} per {unit[i]}" if cells[v][i]["display_price"] is not None
                          else f"item {i} {quoted(cells[v][i])}" for i in ids)
        q.append(f"Item{'s' if len(ids) > 1 else ''} {item_ranges(ids)}: you quoted per {vendor_unit}, but we asked for a price per {u}. "
                 f"We read them as: {reads}. Please confirm these prices per {u}.")
    low = [i for i in open_cell.get("LOW_CONFIDENCE", []) if i not in open_cell.get("AMBIGUOUS_APPLICABILITY", [])]
    for i in low:
        c = cells[v][i]
        q.append(f"Item {i} ({desc[i]}): please confirm the price; we read {quoted(c)}.")
    amb = {}
    for i in open_cell.get("AMBIGUOUS_APPLICABILITY", []):
        amb.setdefault((quoted(cells[v][i]), (cells[v][i]["source"] or {}).get("snippet", "")), []).append(i)
    for (raw, snippet), ids in amb.items():
        options = " or ".join(f"item {i} ({desc[i]})" for i in ids)
        q.append(f"You quoted {raw}" + (f' ("{snippet.strip()}")' if snippet else "") + f". Does this rate apply to {options}, or to both?")
    base = open_cell.get("BASELINE_ASSUMED", [])
    if base:
        q.append(f"You wrote that the other rates are the same as last year. Please confirm prices for items {item_ranges(base)}; "
                 "last year's rates are listed at the end of this email for reference.")
    nq = [i for i, c in cells[v].items() if c["status"] == "not_quoted"]
    if nq:
        q.append(f"You did not quote items {item_ranges(nq)} ({'; '.join(desc[i] for i in nq[:3])}{'...' if len(nq) > 3 else ''}). "
                 "If you can supply them, please send prices.")
    for f in vf.get("UNMATCHED_LINE", []):
        q.append(f"{f['message'].split(', but')[0]}. Which item in our request does this correspond to?")
    if vf.get("DISCOUNT_FOUND"):
        d = (doc.get("discounts") or [{}])[0]
        q.append(f"Your quotation offers a {d.get('percent', 0):g}% discount (\"{(d.get('source_snippet') or '').strip(' *')}\"). "
                 "Please confirm it applies to every item under this annual contract.")
    if vf.get("AMBIGUOUS_TERM"):
        q.append("Please state the delivery (freight) cost to our Pune plant, per drop or per kg, or confirm prices delivered to the plant.")
    ev = doc.get("_evidence", {})
    for f in vf.get("EVIDENCE_MISSING", []):
        q.append("Please send a copy of your valid ISO 9001 certificate." if f["key"].endswith("#iso")
                 else "Please send your burst strength test report for 5-ply BF 22 board.")
    if vf.get("CERT_EXPIRING"):
        q.append("Your ISO 9001 certificate expires during the contract period. Please send the renewed certificate or the renewal date.")
    if vf.get("TEST_SCOPE") or vf.get("REPORT_OLD"):
        q.append("Please send a current burst strength test report for 5-ply BF 22 board (the one received is for a different board or over a year old).")
    if ev and not ev.get("burst_ok") and "not provided" in ev.get("burst", ""):
        q.append("We need your burst strength test report for 5-ply BF 22 board before we can consider your quotation.")
    if ev and not ev.get("iso_ok") and "not certified" in ev.get("iso", "") and doc["questionnaire"]["iso_9001"] != "No":
        q.append("Please confirm whether your plant is ISO 9001 certified, and send the certificate if so.")
    return q, base


def draft(v, short_name, doc, items, cells, vflags, rfq, last_year, who, today=None):
    """One email per vendor, or None when nothing is open for that vendor."""
    q, base = questions(v, doc, items, cells, vflags)
    if not q:
        return None
    today = today or dt.date.today()
    reply_by = today + dt.timedelta(days=3)
    ref = f"RFQ {rfq.get('no')}" if rfq.get("no") else "our request for quotation"
    title = f" ({rfq['title']})" if rfq.get("title") else ""
    lines = [f"Dear {short_name} team,", "",
             f"Thank you for your quotation for {ref}{title}. Before we can finalise the award, please help us with "
             f"the following {'point' if len(q) == 1 else f'{len(q)} points'} by {reply_by:%d %b %Y}:", ""]
    lines += [f"{n}. {x}" for n, x in enumerate(q, 1)]
    if base:
        desc = {it["id"]: it["desc"] for it in items}
        lines += ["", "Last year's rates, for reference:"] + [f"- Item {i} ({desc[i]}): Rs {last_year[i]:g}" for i in base if i in last_year]
    lines += ["", "You can reply to this email with the answers, or send a revised quotation.", "", "Regards,", who or "Buyer"]
    subject = f"{ref}: {len(q)} {'point' if len(q) == 1 else 'points'} to clarify on your quotation"
    return dict(vendor=v, subject=subject, body="\n".join(lines), points=len(q))
