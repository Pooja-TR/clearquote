import io, os, json, re
import pandas as pd
import streamlit as st
from PIL import Image

import extract
from normalize import build_cells, summarise, all_open_flags, vendor_eligibility, attach_evidence
from analyst import ask

HERE = os.path.dirname(__file__)
SAMPLE = os.path.join(HERE, "sample_data")
MAX_AI_CALLS = 120

st.set_page_config(page_title="Quote comparison", page_icon="📦", layout="wide")
S = st.session_state
for k, v in dict(rfx=[], files={}, docs={}, supp_files={}, supports={}, resolutions={}, turns=[], last_year={}, ai_calls=0, outbox=[]).items():
    S.setdefault(k, v)


def load_sample_rfx():
    from openpyxl import load_workbook
    ws = load_workbook(os.path.join(SAMPLE, "00_RFx_line_items_and_questionnaire.xlsx"))["Line items"]
    S.rfx = [dict(id=r[0], desc=r[1], qty=r[2], unit=r[3]) for r in ws.iter_rows(min_row=4, values_only=True) if r[0]]
    ly = load_workbook(os.path.join(SAMPLE, "00_last_year_awarded_rates.xlsx")).active
    S.last_year = {r[0]: r[4] for r in ly.iter_rows(min_row=2, values_only=True) if r[0]}


def load_sample_responses():
    for f in sorted(os.listdir(SAMPLE)):
        if f[:2] in ("A_", "B_", "C_", "D_", "E_"):
            S.files[f] = open(os.path.join(SAMPLE, f), "rb").read()
        elif f.startswith("attach_"):
            S.supp_files[f] = open(os.path.join(SAMPLE, f), "rb").read()


FLAG_NAMES = {"UNIT_MISMATCH": "Unit mismatch", "LOW_CONFIDENCE": "Low-confidence reading", "UNREADABLE_PRICE": "Unreadable price",
              "AMBIGUOUS_APPLICABILITY": "Unclear which line", "BASELINE_ASSUMED": "'Same as last year'", "DISCOUNT_FOUND": "Discount found",
              "AMBIGUOUS_TERM": "Freight / terms", "UNMATCHED_LINE": "Unmatched line", "CURRENCY_UNKNOWN": "Unknown currency",
              "EVIDENCE_MISSING": "Claim without proof", "CERT_EXPIRING": "Certificate expiring", "TEST_SCOPE": "Test on wrong board",
              "REPORT_OLD": "Old test report"}


def pager(total, per_page, key):
    """Previous / next controls. Returns the (start, end) slice for the current page."""
    pages = max(1, -(-total // per_page))
    S[key] = min(max(S.get(key, 1), 1), pages)
    p = S[key]
    if pages > 1:
        c1, c2, c3 = st.columns([1, 3, 1], vertical_alignment="center")
        c1.button("◀ Previous", key=f"{key}_prev", disabled=p == 1, on_click=lambda: S.update({key: p - 1}), width="stretch")
        c2.markdown(f"<div style='text-align:center'>Page {p} of {pages} · showing {(p - 1) * per_page + 1}–{min(p * per_page, total)} of {total}</div>",
                    unsafe_allow_html=True)
        c3.button("Next ▶", key=f"{key}_next", disabled=p == pages, on_click=lambda: S.update({key: p + 1}), width="stretch")
    return (p - 1) * per_page, min(p * per_page, total)


LEGAL_SUFFIXES = r"(,?\s+(pvt\.?|private|ltd\.?|limited|llp|inc\.?|co\.?|company|industries))+\.?$"


def short(v):
    """Display name for a vendor: 'DECCAN CORRUGATORS PVT. LTD.' -> 'Deccan Corrugators'. The full name stays the key everywhere."""
    n = v.title() if v.isupper() else v
    return re.sub(LEGAL_SUFFIXES, "", n, flags=re.I).strip() or n


def fmt_inr(x):
    if x is None:
        return "-"
    if x >= 1e7:
        return f"Rs {x / 1e7:.2f} cr"
    if x >= 1e5:
        return f"Rs {x / 1e5:.2f} lakh"
    return f"Rs {x:,.0f}"


with st.sidebar:
    st.header("Settings")
    fx = st.number_input("USD to INR rate", value=85.0, step=0.5, help="Demo rate. Used by code, never by the model.")
    st.caption("Rate date: demo value, set per RFx in a real deployment.")
    st.caption(f"Main model: {extract.MODEL}. If it is busy or out of free quota, a backup model answers; each file and answer shows which one.")
    st.caption(f"AI calls this session: {S.ai_calls}/{MAX_AI_CALLS}")

# Visual polish only: colours come from .streamlit/config.toml; this adds the header band, card shadows and tab styling.
st.markdown("""<style>
.hero {background: linear-gradient(110deg, #0e0d59 0%, #3d05c6 55%, #9937fb 100%); border-radius: 16px; padding: 26px 30px 22px;
       margin: -8px 0 6px; color: #fff; box-shadow: 0 10px 30px rgba(61, 5, 198, .18);}
.hero h1 {color: #fff; font-size: 2.0rem; font-weight: 700; margin: 0 0 4px; padding: 0; letter-spacing: -.02em;}
.hero p {color: #d9d3ff; margin: 0; font-size: 1.0rem;}
.hero .steps {margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap;}
.hero .steps span {background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.22); border-radius: 999px; padding: 3px 12px;
                   font-size: .82rem; color: #fff;}
.hero .steps span b {color: #ffb37a; font-weight: 600; margin-right: 4px;}
[data-testid="stTabs"] [role="tablist"] {gap: 8px; margin-top: 4px;}
[data-testid="stTabs"] [role="tab"] {padding: 8px 16px; border-radius: 10px 10px 0 0; font-weight: 600; color: #4a4680;}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {background: #ebe8fb; color: #3d05c6;}
[data-testid="stTabs"] [role="tab"]:hover {color: #3d05c6;}
[data-testid="stVerticalBlockBorderWrapper"] {background: #fff; box-shadow: 0 2px 10px rgba(28, 26, 74, .06);}
[data-testid="stMetric"] {background: #fff; border: 1px solid #dcd8f2; border-radius: 12px; padding: 12px 16px;
                          box-shadow: 0 2px 10px rgba(28, 26, 74, .05);}
[data-testid="stMetricValue"] {color: #3d05c6; font-weight: 700;}
[data-testid="stDataFrame"] {background: #fff; border-radius: 10px;}
.legend span {display: inline-block; padding: 2px 10px; border-radius: 6px; margin: 0 6px 4px 0; font-size: .82rem; border: 1px solid #dcd8f2;}
</style>
<div class="hero"><h1>Kill the quote spreadsheet</h1>
<p>Messy vendor quotes in, one defensible comparison out. The model reads; plain code computes every number.</p>
<div class="steps"><span><b>1</b>Draft the RFx</span><span><b>2</b>Read any reply</span><span><b>3</b>Compare and ask</span><span><b>4</b>Resolve flags</span></div>
</div>""", unsafe_allow_html=True)
t1, t2, t3, t4 = st.tabs(["1. Create RFx", "2. Responses", "3. Compare and ask", "4. Review flags"])

# ---------------------------------------------------------------- tab 1
with t1:
    st.write("Describe what you need. The co-pilot drafts line items, a questionnaire and terms. You confirm.")
    brief = st.text_area("What do you need to buy?", placeholder="30 corrugated packaging items for our Pune electronics plant: shipper boxes, mailers, sheets, pallet caps. 60-day payment.", height=90)
    c1, c2 = st.columns(2)
    if c1.button("Draft RFx with AI") and brief:
        if S.ai_calls >= MAX_AI_CALLS:
            st.error("AI call limit reached for this session.")
        else:
            with st.spinner("Drafting..."):
                try:
                    S.ai_calls += 1
                    d = extract.draft_rfx(brief)
                    S.rfx = [dict(id=n, desc=i["description"], unit=i["unit"], qty=i["quantity"]) for n, i in enumerate(d["items"], 1)]
                    S.draft_extra = d
                except Exception as e:
                    st.error(f"Draft failed: {e}")
    if c2.button("Load sample RFx (30 lines)"):
        load_sample_rfx(); st.rerun()
    if S.rfx:
        df = st.data_editor(pd.DataFrame(S.rfx), num_rows="dynamic", width="stretch", hide_index=True, key="items_editor")
        S.rfx = df.dropna(subset=["desc"]).to_dict("records")
        for n, it in enumerate(S.rfx, 1):
            it["id"] = n if not it.get("id") or pd.isna(it.get("id")) else int(it["id"])
        if "draft_extra" in S:
            with st.expander("Questionnaire and terms drafted"):
                st.write(S.draft_extra["questionnaire"]); st.write(S.draft_extra["terms"])
        if st.button("Send to vendors (simulated)"):
            S.outbox = [f"To: vendor{n}@example.com\nSubject: RFQ\n\nPlease quote on the {len(S.rfx)} lines attached. State unit and currency per line." for n in range(1, 6)]
        for m in S.outbox:
            st.code(m, language=None)

# ---------------------------------------------------------------- tab 2
with t2:
    st.write("Vendors reply however they like. Drop in whatever arrives: Excel, PDF, Word, a photo, an email.")
    up = st.file_uploader("Vendor responses", accept_multiple_files=True)
    for f in up or []:
        S.files[f.name] = f.getvalue()
    sup_up = st.file_uploader("Supporting documents: certificates, test reports", accept_multiple_files=True,
                              help="Matched to vendors by the company name printed on each document. You can reassign any that do not match.")
    for f in sup_up or []:
        S.supp_files[f.name] = f.getvalue()
    if st.button("Load the 5 sample responses"):
        if not S.rfx:
            load_sample_rfx()
        load_sample_responses(); st.rerun()
    st.caption("Samples include an Excel in its own layout, a PDF, a Word letter, an angled phone photo and a one-line email, "
               "plus 5 attached ISO certificates and burst test reports.")
    if S.files:
        st.dataframe(pd.DataFrame([{"File": n, "Type": extract.kind_of(n), "Size": f"{len(b) / 1024:.1f} KB",
                                    "Read": any(d.get("_file") == n for d in S.docs.values()),
                                    "Read by": next((d.get("_model", "") for d in S.docs.values() if d.get("_file") == n), "")} for n, b in S.files.items()]),
                     hide_index=True, width="stretch")
        if st.button("Read responses with AI", type="primary"):
            if not S.rfx:
                st.error("Create or load the RFx first (tab 1).")
            else:
                bar = st.progress(0.0)
                names = list(S.files)
                for name, data in S.supp_files.items():
                    if name in S.supports:
                        continue
                    st.write(f"Reading {name} ...")
                    try:
                        if not extract.support_is_cached(data):
                            if S.ai_calls >= MAX_AI_CALLS:
                                raise RuntimeError("AI call limit reached for this session")
                            S.ai_calls += 1
                        S.supports[name] = extract.extract_support(name, data)
                    except Exception as e:
                        st.error(f"{name}: {e}")
                for n, name in enumerate(names):
                    if any(d.get("_file") == name for d in S.docs.values()):
                        continue
                    st.write(f"Reading {name} ...")
                    try:
                        if not extract.is_cached(S.files[name], S.rfx):
                            if S.ai_calls >= MAX_AI_CALLS:
                                raise RuntimeError("AI call limit reached for this session")
                            S.ai_calls += 1
                        doc = extract.extract_response(name, S.files[name], S.rfx)
                        S.docs[doc["vendor_name"]] = doc
                    except Exception as e:
                        st.error(f"{name}: {e}")
                    bar.progress((n + 1) / len(names))
                st.rerun()
    if S.supp_files:
        st.markdown("**Supporting documents**")
        _, unassigned = attach_evidence(S.docs, S.supports, S.resolutions) if S.docs else ({}, [])
        def owner(n):
            r = S.resolutions.get(f"support|{n}")
            if r:
                return short(r["vendor"]) + " (set by you)"
            from normalize import match_vendor
            sd = S.supports.get(n)
            v = match_vendor(sd["issued_to"], list(S.docs)) if sd and S.docs else None
            return short(v) if v else ("no match" if sd else "")
        def facts(sd):
            if sd["doc_type"] == "iso_certificate":
                return f"valid to {sd.get('valid_until') or 'not shown'}"
            if sd["doc_type"] == "test_report":
                return f"{sd.get('measured_value')} vs min {sd.get('spec_min')}, {sd.get('stated_result') or '?'}"
            return sd.get("title") or ""
        st.dataframe(pd.DataFrame([{"File": n, "Kind": (S.supports[n]["doc_type"].replace("_", " ") if n in S.supports else "not read yet"),
                                    "Issued to": S.supports[n]["issued_to"] if n in S.supports else "", "Matched vendor": owner(n),
                                    "Key facts": facts(S.supports[n]) if n in S.supports else ""} for n in S.supp_files]),
                     hide_index=True, width="stretch")
        for sd in unassigned:
            with st.container(border=True):
                st.write(f"**{sd['_file']}** is issued to '{sd['issued_to']}', which matches no vendor. Which vendor sent it?")
                pick = st.selectbox("Vendor", list(S.docs), format_func=short, key=f"assign_{sd['_file']}")
                if st.button("Assign", key=f"assignb_{sd['_file']}"):
                    S.resolutions[f"support|{sd['_file']}"] = {"vendor": pick}; st.rerun()

def show_crop(vendor, c):
    """Show the region of a photo a value was read from (box_2d is 0-1000 [ymin, xmin, ymax, xmax])."""
    fname = docs[vendor].get("_file")
    if not (c["source"] and c["source"].get("box") and fname in S.files and docs[vendor].get("_kind") == "image"):
        return
    try:
        im = Image.open(io.BytesIO(S.files[fname]))
        y0, x0, y1, x1 = c["source"]["box"]
        W, H = im.size
        from PIL import ImageDraw
        im = im.convert("RGB")
        bx = (x0 * W // 1000, y0 * H // 1000, x1 * W // 1000, y1 * H // 1000)
        ImageDraw.Draw(im).rectangle((bx[0] - 6, bx[1] - 6, bx[2] + 6, bx[3] + 6), outline=(230, 120, 0), width=4)
        # Photos are often tilted, so a thin strip can pair a price with the wrong row label. Show a taller band of the
        # full table width with the value boxed, so the buyer can follow the ruled lines to the item name.
        pad = max(70, (bx[3] - bx[1]) * 3)
        crop = im.crop((W // 25, max(0, bx[1] - pad), W - W // 25, min(H, bx[3] + pad)))
        st.image(crop, caption="The value read is boxed in orange. Phone photos are often tilted: follow the table lines to the item, not straight across.", width="stretch")
    except Exception:
        st.caption("Could not crop the photo region.")


# ---------------------------------------------------------------- shared compute
items = S.rfx
docs = attach_evidence(S.docs, S.supports, S.resolutions)[0] if S.docs else {}
cells = vflags = None
if docs and items:
    cells, vflags = build_cells(docs, items, fx, S.last_year, S.resolutions)

# ---------------------------------------------------------------- tab 3
with t3:
    if not cells:
        st.info("Add the RFx and read the responses first.")
    else:
        rows, ready = summarise(docs, items, cells, vflags)
        nopen = len(all_open_flags(cells, vflags))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Comparison ready", f"{ready:.0%}")
        m2.metric("Flags needing a decision", nopen, help="Resolve them in tab 4. Unresolved prices are excluded from every total.")
        m3.metric("Pass quality gate", f"{sum(r['eligible'] for r in rows)} of {len(rows)}", help="ISO 9001 and a burst test report provided now.")
        m4.metric("Usable prices", f"{sum(c['price'] is not None for v in docs for c in cells[v].values())} of {len(docs) * len(items)}",
                  help="Vendor x line cells with a confirmed or converted price. Everything else is excluded from totals.")
        st.progress(ready)
        vend = list(docs)
        grid, sty = [], []
        for it in items:
            r, s = {"Item": f"{it['id']}. {it['desc']}", "Unit": it["unit"], "Qty": it["qty"]}, {}
            for v in vend:
                c = cells[v][it["id"]]
                if c["status"] == "ok":
                    r[v], s[v] = f"{c['price']:,.2f}", ""
                elif c["status"] == "converted":
                    r[v], s[v] = f"{c['price']:,.2f}", "background-color:#ebe6ff"
                elif c["status"] in ("blocked", "assumed"):
                    d = c["display_price"]
                    r[v], s[v] = (f"? {d:,.2f}" if d is not None else "? unreadable" if c.get("unreadable") else "? blocked"), "background-color:#ffe9d6"
                else:
                    r[v], s[v] = "not quoted", "background-color:#eeeeee;color:#777"
            r = {short(k) if k in docs else k: x for k, x in r.items()}
            s = {short(k): x for k, x in s.items()}
            grid.append(r); sty.append(s)
        gdf = pd.DataFrame(grid)
        st.markdown('<div class="legend"><span style="background:#fff">confirmed</span><span style="background:#ebe6ff">converted from USD</span>'
                    '<span style="background:#ffe9d6">? needs your decision, excluded from totals</span>'
                    '<span style="background:#eeeeee;color:#777">not quoted</span> <small>Prices are Rs per RFx unit.</small></div>', unsafe_allow_html=True)
        st.dataframe(gdf.style.apply(lambda _: pd.DataFrame([{**{"Item": "", "Unit": "", "Qty": ""}, **s} for s in sty], columns=gdf.columns), axis=None),
                     width="stretch", hide_index=True, height=min(35 * (len(items) + 1) + 3, 1100),
                     column_config={"Item": st.column_config.TextColumn(width=240, pinned=True), "Unit": st.column_config.TextColumn(width=86), "Qty": st.column_config.NumberColumn(format="localized")})
        vdf = pd.DataFrame([{"Vendor": short(r["vendor"]), "Quality gate": ("Pass" if r["eligible"] else f"Fail: {r['eligibility_note']}"),
                             "Lines usable": f"{r['lines_usable']} of {len(items)}", "Total of usable lines": fmt_inr(r["total"]),
                             "Open flags": r["open_flags"]} for r in rows])
        st.dataframe(vdf.style.map(lambda x: "color:#11743b;font-weight:600" if x == "Pass" else "color:#b42318;font-weight:600", subset=["Quality gate"]),
                     hide_index=True, width="stretch")
        st.caption("Totals cover usable lines only. A vendor with fewer usable lines is not directly comparable.")
        st.markdown("**Quality evidence**: what each vendor claimed, and what the attached documents prove")
        edf = pd.DataFrame([{"Vendor": short(v), "ISO 9001 claimed": d["questionnaire"]["iso_9001"], "ISO 9001 evidence": d["_evidence"]["iso"],
                             "Burst report claimed": d["questionnaire"]["burst_report"], "Burst test evidence": d["_evidence"]["burst"],
                             "Gate": "Pass" if vendor_eligibility(d)[0] else "Fail"} for v, d in docs.items()])
        weak = lambda x: "color:#b45309;font-weight:600" if ("claimed, no" in str(x) or "during contract" in str(x)) else ("color:#b42318;font-weight:600" if any(w in str(x) for w in ("expired", "FAIL", "not ")) else "")
        st.dataframe(edf.style.map(weak, subset=["ISO 9001 evidence", "Burst test evidence"])
                        .map(lambda x: "color:#11743b;font-weight:600" if x == "Pass" else "color:#b42318;font-weight:600", subset=["Gate"]),
                     hide_index=True, width="stretch",
                     column_config={"Vendor": st.column_config.TextColumn(width=150), "ISO 9001 claimed": st.column_config.TextColumn("ISO claimed", width=95),
                                    "Burst report claimed": st.column_config.TextColumn("Burst claimed", width=105), "Gate": st.column_config.TextColumn(width=60)})

        with st.expander("Inspect any price: where it came from"):
            ic1, ic2 = st.columns(2)
            pick = ic1.selectbox("Line", [f"{it['id']}. {it['desc']}" for it in items])
            vsel = ic2.selectbox("Vendor", vend, format_func=short)
            c = cells[vsel][int(pick.split(".")[0])]
            st.write(f"Status: {c['status']}")
            if c["raw"]:
                st.write(f"As quoted: {c['raw']}")
                st.write(f"Calculation: {c['receipt']}")
            if c["source"]:
                st.write(f"Source: {c['source'].get('ref')}")
                st.code(c["source"].get("snippet") or "", language=None)
                show_crop(vsel, c)
            for f in c["flags"]:
                st.write(f"Flag {f['type']}: {f['message']}")

        st.subheader("Ask about the comparison")
        qs = {"Missing lines": "Which vendors did not quote all lines, and which lines are missing?",
              "Cheapest per line (chart)": "Who is cheapest per line? Show it as a chart.",
              "Freight with no number": "Which quotes have freight or other charges we have no number for?",
              "Split award and saving": "If we split the award to the cheapest vendor per line, but only among vendors who passed the quality questions, what is the total and the saving versus last year?",
              "What is unresolved?": "What is still unresolved, and could any of it change the award?"}
        picked = st.pills("Suggested questions", list(qs), key=f"sugg{len(S.turns)}", help="Click to ask. Hover a finished answer's question to see it in full.")
        with st.form("ask_form", clear_on_submit=True, border=False):
            fc1, fc2 = st.columns([6, 1], vertical_alignment="bottom")
            typed = fc1.text_input("Ask anything about this comparison", placeholder="e.g. Compare Shree and Maruti on 7-ply boxes")
            sent = fc2.form_submit_button("Ask", type="primary", width="stretch")
        question = (typed.strip() if sent and typed.strip() else None) or (qs[picked] if picked else None)
        if question:
            if S.ai_calls >= MAX_AI_CALLS:
                st.error("AI call limit reached for this session.")
            else:
                hist = [x for t in S.turns[-3:] for x in (("user", t["q"]), ("assistant", t["a"]))]
                with st.spinner("Analysing..."):
                    try:
                        S.ai_calls += 1
                        ans, trace, model = ask(question, hist, docs, items, cells, vflags, S.last_year)
                    except Exception as e:
                        ans, trace, model = f"The analyst call failed: {e}", [], None
                S.turns.append(dict(n=len(S.turns) + 1, q=question, a=ans, trace=trace, model=model,
                                    at=pd.Timestamp.now(tz="Asia/Kolkata").strftime("%H:%M IST")))
                st.rerun()

        if S.turns:
            hc1, hc2 = st.columns([3, 2], vertical_alignment="bottom")
            hc1.markdown(f"**Answers** ({len(S.turns)}, newest first)")
            needle = hc2.text_input("Filter answers", placeholder="Filter by a word, e.g. freight", label_visibility="collapsed",
                                    key="turn_filter", on_change=lambda: S.update(turn_page=1))
            shown = [t for t in reversed(S.turns) if not needle or needle.lower() in (t["q"] + " " + t["a"]).lower()]
            if not shown:
                st.caption("No answers match that filter.")
            a, b = pager(len(shown), 5, "turn_page")
            for t in shown[a:b]:
                with st.container(border=True):
                    st.markdown(f"**Q{t['n']}. {t['q']}**")
                    st.caption(f"{t['at']} · answered by {t['model'] or 'no model (failed)'} · every number comes from the tools listed under 'Show calculation'")
                    st.markdown(t["a"])
                    for k, tr in enumerate(t["trace"]):
                        res = tr["result"]
                        if isinstance(res.get("table"), list) and res["table"]:
                            with st.expander(f"Table: {tr['tool'].replace('_', ' ')} ({len(res['table'])} rows)"):
                                st.dataframe(pd.DataFrame(res["table"]), hide_index=True, width="stretch")
                                st.download_button("Export CSV", pd.DataFrame(res["table"]).to_csv(index=False),
                                                   file_name=f"Q{t['n']}_{tr['tool']}.csv", key=f"dl{t['n']}_{k}")
                        if res.get("chart") and res["chart"].get("data"):
                            cdf = pd.DataFrame(res["chart"]["data"])
                            cdf["label"] = [short(x) if x in docs else x for x in cdf["label"]]
                            title = res["chart"]["title"]
                            if "(INR)" in title:
                                cdf["value"] = cdf["value"] / 1e5; title = title.replace("(INR)", "(Rs lakh)")
                            st.caption(title)
                            st.bar_chart(cdf.set_index("label"), horizontal=True, height=60 + 40 * len(cdf), color="#7f39ff")
                    if t["trace"]:
                        with st.expander("Show calculation"):
                            for tr in t["trace"]:
                                st.markdown(f"**{tr['tool']}** `{tr['args']}`")
                                st.write(tr["result"].get("calculation", ""))
                                for cv in tr["result"].get("caveats", []):
                                    st.write(f"- {cv}")

# ---------------------------------------------------------------- tab 4
with t4:
    if not cells:
        st.info("Nothing to review yet.")
    else:
        fl = all_open_flags(cells, vflags)
        if not fl:
            st.success("No open flags. Every price in the comparison is confirmed, converted or marked not quoted.")
        else:
            st.write(f"**{len(fl)} open flags.** Resolving one recalculates the comparison and the analyst's answers.")
            reset = lambda: S.update(flag_page=1)
            counts = {}
            for f in fl:
                counts[f["type"]] = counts.get(f["type"], 0) + 1
            type_opts = {f"{FLAG_NAMES.get(k, k)} ({c})": k for k, c in sorted(counts.items(), key=lambda kv: -kv[1])}
            pick_types = st.pills("Flag type", list(type_opts), selection_mode="multi", key="flag_types", on_change=reset,
                                  help="Pick one or more types. None selected shows all.")
            vcounts = {}
            for f in fl:
                vcounts[f["vendor"]] = vcounts.get(f["vendor"], 0) + 1
            vopts = {"All vendors": None, **{f"{short(v)} ({c})": v for v, c in vcounts.items()}}
            pick_v = st.pills("Vendor", list(vopts), key="flag_vendor", on_change=reset, default="All vendors")
            want_types = {type_opts[x] for x in pick_types or []}
            want_v = vopts.get(pick_v)
            shown = [f for f in fl if (not want_types or f["type"] in want_types) and (not want_v or f["vendor"] == want_v)]
            if not shown:
                st.caption("No open flags match these filters.")
            a, b = pager(len(shown), 10, "flag_page")
            for f in shown[a:b]:
                k = f["key"]
                with st.container(border=True):
                    where = f"line {f['item_id']}. {next((it['desc'] for it in items if it['id'] == f['item_id']), '')}" if f["item_id"] else "whole quote"
                    st.markdown(f"**{FLAG_NAMES.get(f['type'], f['type'])}** · {short(f['vendor'])} · {where}")
                    st.write(f["message"])
                    if f["item_id"]:
                        c = cells[f["vendor"]][f["item_id"]]
                        if c["source"]:
                            st.caption(f"Source {c['source'].get('ref')}: {c['source'].get('snippet')}")
                            show_crop(f["vendor"], c)
                    b1, b2 = st.columns(2)
                    if f["type"] in ("LOW_CONFIDENCE", "UNIT_MISMATCH", "CURRENCY_UNKNOWN", "BASELINE_ASSUMED", "UNREADABLE_PRICE") and f["item_id"]:
                        c = cells[f["vendor"]][f["item_id"]]
                        val = b2.number_input("Enter the correct Rs price per RFx unit", value=c["display_price"], min_value=0.0, step=0.5,
                                              placeholder="Type the price you read", key=f"v_{k}")
                        if b2.button("Use my value", key=f"e_{k}", disabled=not val):
                            S.resolutions[k] = dict(action="edit", price=val); st.rerun()
                    if f["type"] == "UNMATCHED_LINE":
                        target = b2.selectbox("It belongs to", [f"{it['id']}. {it['desc']}" for it in items], key=f"m_{k}")
                        if b2.button("Match to this line", key=f"mb_{k}"):
                            S.resolutions[k] = dict(action="map", item_id=int(target.split(".")[0])); st.rerun()
                    if f["type"] != "UNREADABLE_PRICE":
                        label = ("Leave it out" if f["type"] == "UNMATCHED_LINE" else "Accept the claim" if f["type"] == "EVIDENCE_MISSING"
                                 else "Confirm as shown" if f["item_id"] else "Accept")
                        if b1.button(label, key=f"a_{k}"):
                            S.resolutions[k] = dict(action="accept"); st.rerun()
