"""Analyst tools. The model chooses which tool to call; plain code computes every number it quotes."""
import os
from normalize import vendor_eligibility, summarise, all_open_flags

SYSTEM = """You are a procurement analyst helping a category buyer award a contract.
Rules:
- Every number in your answer must come from a tool result. Never calculate totals, savings or averages yourself.
- Call the tools you need, then write a short answer: lead with the result, then a one-line 'Calculation' (which tool, which filter) and 'Caveats' (flags that excluded data, unquoted lines, unconfirmed values).
- If a tool result says lines were excluded or blocked, say so plainly. Never fill a gap with an estimate.
- If a question cannot be answered from the data, say what is missing.
- Answer every part of the question. If it asks whether something should change the decision, weigh the quality gate and open flags, then say yes or no and why.
- Amounts are in INR. Use lakh/crore formatting for large amounts (Rs 38.4 lakh).
- Keep answers concise. The interface already shows the tables and charts from the tool results: never draw charts or long tables in text.
- A line 'assumed from last year' was NOT quoted by the vendor. Never count it as quoted."""


def make_tools(docs, items, cells, vflags, last_year, trace):
    qty = {i["id"]: i["qty"] for i in items}
    desc = {i["id"]: i["desc"] for i in items}
    unit = {i["id"]: i["unit"] for i in items}
    vendors = list(docs)
    elig = {v: vendor_eligibility(docs[v])[0] for v in vendors}

    def find_vendor(name):
        n = (name or "").lower().strip()
        hits = [v for v in vendors if n and n in v.lower()]
        return hits[0] if hits else None

    def price_of(v, i, include_low):
        c = cells[v][i]
        if c["price"] is not None:
            return c["price"]
        return c["scenario_price"] if include_low else None

    def log(name, args, res):
        trace.append(dict(tool=name, args=args, result=res))
        return res

    def excluded_notes(only_eligible, include_low):
        notes = []
        for v in vendors:
            if only_eligible and not elig[v]:
                notes.append(f"{v}: excluded by quality gate ({vendor_eligibility(docs[v])[1]}).")
                continue
            blocked = [i for i in cells[v] if cells[v][i]["status"] in ("blocked", "assumed") and price_of(v, i, include_low) is None]
            unreadable = [i for i in blocked if cells[v][i].get("unreadable")]
            blocked = [i for i in blocked if i not in unreadable]
            if unreadable:
                notes.append(f"{v}: price unreadable in the source for item(s) {', '.join(map(str, unreadable))}; no value exists to test, the buyer must enter it.")
            if blocked:
                notes.append(f"{v}: {len(blocked)} line(s) unusable until flags are resolved (items {', '.join(map(str, blocked[:8]))}{'...' if len(blocked) > 8 else ''}).")
            nq = [i for i in cells[v] if cells[v][i]["status"] == "not_quoted"]
            if nq:
                notes.append(f"{v}: did not quote {len(nq)} line(s) (items {', '.join(map(str, nq[:8]))}{'...' if len(nq) > 8 else ''}).")
        return notes

    def vendor_overview() -> dict:
        """Coverage and status per vendor: lines actually quoted, lines only assumed from 'same as last year', lines usable, quality gate result, open flags, total of usable lines."""
        rows, _ = summarise(docs, items, cells, vflags)
        table = [dict(vendor=r["vendor"], lines_quoted=r["lines_quoted"], lines_assumed_from_last_year=r["lines_assumed_from_last_year"],
                      lines_usable=r["lines_usable"], of_total=len(items),
                      passes_quality_gate=r["eligible"], gate_note=r["eligibility_note"], open_flags=r["open_flags"],
                      total_of_usable_lines_inr=round(r["total"]),
                      quoted_items=[i for i, c in cells[r["vendor"]].items() if c["status"] != "not_quoted" and not c["receipt"].startswith("Assumed")],
                      assumed_from_last_year_items=[i for i, c in cells[r["vendor"]].items() if c["receipt"].startswith("Assumed")],
                      not_quoted_items=[i for i, c in cells[r["vendor"]].items() if c["status"] == "not_quoted"],
                      unusable_items=[i for i, c in cells[r["vendor"]].items() if c["status"] in ("blocked", "assumed")]) for r in rows]
        return log("vendor_overview", {}, dict(table=table, calculation="Counts of usable cells per vendor; total = sum(quantity x price) over usable lines only.",
                                              caveats=["Totals cover usable lines only and are not comparable between vendors with different coverage."]))

    def lowest_price_per_item(only_eligible: bool = False, include_low_confidence: bool = False) -> dict:
        """Cheapest usable price for each RFx line, with the winning vendor and the runner-up. Set only_eligible true to restrict to vendors who passed the quality gate."""
        rows, chart = [], []
        for it in items:
            i = it["id"]
            cands = sorted([(price_of(v, i, include_low_confidence), v) for v in vendors
                            if (elig[v] or not only_eligible) and price_of(v, i, include_low_confidence) is not None])
            if not cands:
                rows.append(dict(item_id=i, item=desc[i], unit=unit[i], best_vendor=None, best_price_inr=None, runner_up=None)); continue
            rows.append(dict(item_id=i, item=desc[i], unit=unit[i], best_vendor=cands[0][1], best_price_inr=round(cands[0][0], 2),
                             runner_up=f"{cands[1][1]} ({cands[1][0]:.2f})" if len(cands) > 1 else None))
        wins = {}
        for r in rows:
            if r["best_vendor"]:
                wins[r["best_vendor"]] = wins.get(r["best_vendor"], 0) + 1
        chart = [dict(label=k, value=v) for k, v in wins.items()]
        return log("lowest_price_per_item", dict(only_eligible=only_eligible, include_low_confidence=include_low_confidence),
                   dict(table=rows, chart=dict(title="Lines won per vendor", data=chart),
                        calculation=f"For each line, the minimum usable price across {'quality-gate-passing' if only_eligible else 'all'} vendors.",
                        caveats=excluded_notes(only_eligible, include_low_confidence)))

    def split_award(only_eligible: bool = True, include_low_confidence: bool = False) -> dict:
        """Award every line to its cheapest usable vendor (split award). Returns the winner per line, total cost at annual quantities, last-year cost, savings, and single-vendor award totals for comparison. Defaults to quality-gate vendors only."""
        rows, total, ly_total, uncovered = [], 0.0, 0.0, []
        by_vendor = {}
        for it in items:
            i = it["id"]
            cands = sorted([(price_of(v, i, include_low_confidence), v) for v in vendors
                            if (elig[v] or not only_eligible) and price_of(v, i, include_low_confidence) is not None])
            if not cands:
                uncovered.append(i); continue
            p, v = cands[0]
            cost = p * qty[i]
            total += cost; ly_total += last_year.get(i, 0) * qty[i]
            by_vendor[v] = by_vendor.get(v, 0) + cost
            rows.append(dict(item_id=i, item=desc[i], qty=qty[i], vendor=v, price_inr=round(p, 2), cost_inr=round(cost)))
        singles = {}
        for v in vendors:
            if (elig[v] or not only_eligible) and all(price_of(v, it["id"], include_low_confidence) is not None for it in items):
                singles[v] = round(sum(qty[it["id"]] * price_of(v, it["id"], include_low_confidence) for it in items))
        res = dict(table=rows, total_inr=round(total), by_vendor_inr={k: round(v) for k, v in by_vendor.items()},
                   last_year_cost_same_lines_inr=round(ly_total), saving_vs_last_year_inr=round(ly_total - total),
                   saving_vs_last_year_pct=round(100 * (ly_total - total) / ly_total, 1) if ly_total else None,
                   single_award_totals_inr=singles,
                   best_single_award_saving_inr=(round(min(singles.values()) - total) if singles else None),
                   lines_with_no_usable_price=uncovered,
                   chart=dict(title="Award value by vendor (INR)", data=[dict(label=k, value=round(v)) for k, v in by_vendor.items()]),
                   calculation=f"Per line: min usable price among {'quality-gate-passing' if only_eligible else 'all'} vendors x annual quantity; summed. "
                               f"Last-year cost uses last year's rate x same quantity for the awarded lines.",
                   caveats=excluded_notes(only_eligible, include_low_confidence) +
                           ([f"{len(uncovered)} line(s) have no usable price and are left out of the total."] if uncovered else []))
        return log("split_award", dict(only_eligible=only_eligible, include_low_confidence=include_low_confidence), res)

    def compare_vendors(vendor_a: str, vendor_b: str, item_filter: str = "") -> dict:
        """Side-by-side prices of two vendors for lines whose description contains item_filter (for example '5-ply'). Vendor names can be partial."""
        a, b = find_vendor(vendor_a), find_vendor(vendor_b)
        if not a or not b:
            return log("compare_vendors", dict(vendor_a=vendor_a, vendor_b=vendor_b), dict(error=f"Unknown vendor. Known: {vendors}"))
        rows = []
        for it in items:
            if item_filter.lower() in it["desc"].lower():
                i = it["id"]
                pa, pb = cells[a][i]["price"], cells[b][i]["price"]
                rows.append(dict(item_id=i, item=desc[i], **{a: pa and round(pa, 2), b: pb and round(pb, 2)},
                                 diff_inr=(round(pa - pb, 2) if pa is not None and pb is not None else None),
                                 note_a=cells[a][i]["receipt"][:90], note_b=cells[b][i]["receipt"][:90]))
        return log("compare_vendors", dict(vendor_a=a, vendor_b=b, item_filter=item_filter),
                   dict(table=rows, calculation="Normalised INR price per RFx unit for each vendor; diff = first minus second.",
                        caveats=["Blank means not quoted or unresolved flag; see note columns."]))

    def open_flags(vendor: str = "") -> dict:
        """List unresolved flags (unit mismatches, low-confidence readings, unconfirmed baselines, discounts awaiting acceptance, freight with no amount). Optionally filter by vendor."""
        v = find_vendor(vendor) if vendor else None
        fl = [f for f in all_open_flags(cells, vflags) if not v or f["vendor"] == v]
        table = [dict(vendor=f["vendor"], item_id=f["item_id"] or None, flag=f["type"], message=f["message"]) for f in fl]
        return log("open_flags", dict(vendor=vendor), dict(table=table, count=len(table), calculation="All flags not yet accepted or resolved by the buyer.", caveats=[]))

    def vendor_terms() -> dict:
        """Commercial terms and questionnaire answers per vendor: freight, payment, validity, GST, lead time, ISO, burst report, FSC, 60-day payment."""
        table = []
        for v in vendors:
            d = docs[v]; t, q = d["terms"], d["questionnaire"]
            table.append(dict(vendor=v, freight=t.get("freight"), freight_included=t.get("freight_included"), freight_amount_stated=t.get("freight_amount_stated"),
                              payment=t.get("payment_terms"), validity=t.get("validity"), gst=t.get("gst"), lead_time_days=q.get("lead_time_days"),
                              iso_9001=q["iso_9001"], burst_report=q["burst_report"], fsc=q["fsc"], accepts_60_day=q["accepts_60_day_payment"]))
        return log("vendor_terms", {}, dict(table=table, calculation="Terms and questionnaire answers as extracted, with source text kept in the grid inspector.",
                                            caveats=["'Not stated' means the vendor did not say, not that the answer is No."]))

    return [vendor_overview, lowest_price_per_item, split_award, compare_vendors, open_flags, vendor_terms]


def ask(question, history, docs, items, cells, vflags, last_year):
    """history: list of (role, text). Returns (answer_text, trace, model_used)."""
    from google import genai
    from google.genai import types
    from extract import generate
    trace = []
    tools = make_tools(docs, items, cells, vflags, last_year, trace)
    contents = [types.Content(role=("user" if r == "user" else "model"), parts=[types.Part.from_text(text=t)]) for r, t in history]
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))
    resp, used = generate(contents, types.GenerateContentConfig(system_instruction=SYSTEM, tools=tools, temperature=0.2),
                       before_attempt=trace.clear)
    return (resp.text or "I could not produce an answer. Try rephrasing."), trace, used
