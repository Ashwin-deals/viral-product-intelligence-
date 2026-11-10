"""Build the Group 13 capstone final report (DOCX, under 25 pages) on top of the supplied cover page.

Run from the repo root after reports/unit2_surge_classification.py and reports/make_report_figures.py:

    python reports/build_report.py [path/to/Cover Page.docx]
"""
import re
import sys
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "outputs"
FIG = ROOT / "docs" / "figures"
DASH = OUT / "dashboard"
COVER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Downloads" / "Cover Page.docx"
TARGET = ROOT / "reports" / "Group13_Viral_Product_Intelligence_Final_Report.docx"

NAVY = RGBColor(0x1F, 0x38, 0x64)
GREY = RGBColor(0x59, 0x59, 0x59)
FONT = "Times New Roman"
REPO = "https://github.com/Ashwin-deals/viral-product-intelligence-"

TEAM = [
    ("Mounik Sai", "23CSE561", "Method 1 Forecasting, Method 2 Isolation & Viral Radar"),
    ("Ashwin E", "23CSE09", "Data Collection Pipeline, Text Corpus & Text Mining"),
    ("Vijay Krishna", "23CSE112", "Master Dataset, EDA & Text-Mining Evaluation"),
    ("Sisthick S", "23CSE465", "Feature Engineering, Scaling & Streamlit Dashboard"),
    ("Konduru Hemesh", "23CSE724", "Time-Series Preparation & Pipeline Consolidation"),
]


# ----------------------------------------------------------------------------- low-level helpers
def set_run_font(run, size=None, bold=None, italic=None, color=None):
    run.font.name = FONT
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), FONT)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color is not None:
        run.font.color.rgb = color


def add_field(paragraph, instr, placeholder=""):
    """Insert a complex field (TOC, SEQ, PAGE) that Word updates on export."""
    def fld(kind):
        el = OxmlElement("w:fldChar")
        el.set(qn("w:fldCharType"), kind)
        if kind == "begin":
            el.set(qn("w:dirty"), "true")
        return el

    runs = []
    for part in ("begin", "instr", "separate", "text", "end"):
        r = paragraph.add_run(placeholder if part == "text" else None)
        if part == "instr":
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = f" {instr} "
            r._element.append(it)
        elif part != "text":
            r._element.append(fld(part))
        set_run_font(r)
        runs.append(r)
    return runs[3]


def shade(cell, hex_color):
    tcpr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcpr.append(shd)


def repeat_header(row):
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(el)


INLINE = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)")


def add_rich(paragraph, text, size=None, color=None):
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**"):
            set_run_font(paragraph.add_run(part[2:-2]), size=size, bold=True, color=color)
        elif part.startswith("`"):
            r = paragraph.add_run(part[1:-1])
            set_run_font(r, size=(size or 12) - 1.5, color=color)
            r._element.rPr.rFonts.set(qn("w:ascii"), "Consolas")
            r._element.rPr.rFonts.set(qn("w:hAnsi"), "Consolas")
        elif part.startswith("*"):
            set_run_font(paragraph.add_run(part[1:-1]), size=size, italic=True, color=color)
        else:
            set_run_font(paragraph.add_run(part), size=size, color=color)


# ----------------------------------------------------------------------------- report writer
class Report:
    def __init__(self, doc):
        self.doc = doc
        self.figures = []  # (number, caption, section, source)
        self.section = ""
        self.counters = {"Figure": 0, "Table": 0}

    def h1(self, text, page_break=False, in_toc=True):
        p = self.doc.add_paragraph(style="Heading 1" if in_toc else "TOC Heading")
        p.paragraph_format.page_break_before = page_break
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_before = Pt(0 if page_break else 12)
        p.paragraph_format.space_after = Pt(6)
        add_rich(p, text, size=15, color=NAVY)
        self.section = text.split(" ")[0]

    def h2(self, text):
        p = self.doc.add_paragraph(style="Heading 2")
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(3)
        add_rich(p, text, size=13, color=NAVY)

    def p(self, text, size=12, spacing=1.5, after=4, align=WD_ALIGN_PARAGRAPH.JUSTIFY):
        para = self.doc.add_paragraph(style="Normal")
        add_rich(para, text, size=size)
        para.alignment = align
        fmt = para.paragraph_format
        fmt.line_spacing = spacing
        fmt.space_after = Pt(after)
        fmt.space_before = Pt(0)
        return para

    def bullets(self, items, size=12, spacing=1.5):
        for item in items:
            para = self.doc.add_paragraph(style="List Paragraph")
            fmt = para.paragraph_format
            fmt.left_indent = Inches(0.3)
            fmt.first_line_indent = Inches(-0.2)
            fmt.line_spacing = spacing
            fmt.space_after = Pt(1)
            para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            set_run_font(para.add_run("•\u00a0\u00a0"), size=size)
            add_rich(para, item, size=size)

    def caption(self, kind, text, source=None):
        self.counters[kind] += 1
        para = self.doc.add_paragraph(style="Caption")
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_run_font(para.add_run(f"{kind} "), size=10, bold=True, italic=False, color=NAVY)
        set_run_font(add_field(para, f"SEQ {kind} \\* ARABIC", str(self.counters[kind])), size=10, bold=True,
                     italic=False, color=NAVY)
        set_run_font(para.add_run(". "), size=10, bold=True, italic=False, color=NAVY)
        set_run_font(para.add_run(text), size=10, bold=False, italic=False, color=RGBColor(0x26, 0x26, 0x26))
        if source:
            r = para.add_run(f"  Source: {source.replace('`', '')}")
            set_run_font(r, size=8.5, bold=False, italic=True, color=GREY)
        para.paragraph_format.space_before = Pt(2)
        para.paragraph_format.space_after = Pt(6)
        return para

    def figure(self, path, caption, source, width=5.0, note=None):
        para = self.doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.paragraph_format.space_before = Pt(4)
        para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.keep_with_next = True
        para.add_run().add_picture(str(path), width=Inches(width))
        self.caption("Figure", caption, source)
        n = self.counters["Figure"]
        self.figures.append((n, caption, self.section, source))
        if note:
            self.p(f"**Interpretation.** {note}")
        return n

    def table(self, headers, rows, caption, widths=None, size=9, note=None, bold_first_col=False):
        cap = self.caption("Table", caption)
        cap.paragraph_format.keep_with_next = True
        n = self.counters["Table"]
        t = self.doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        repeat_header(t.rows[0])
        for i, h in enumerate(headers):
            cell = t.rows[0].cells[i]
            add_rich(cell.paragraphs[0], f"**{h}**", size=size, color=RGBColor(0xFF, 0xFF, 0xFF))
            shade(cell, "1F3864")
        for ri, row in enumerate(rows):
            cells = t.add_row().cells
            for i, val in enumerate(row):
                txt = str(val)
                if bold_first_col and i == 0 and not txt.startswith("**"):
                    txt = f"**{txt}**"
                add_rich(cells[i].paragraphs[0], txt, size=size)
                if ri % 2 == 1:
                    shade(cells[i], "EAF0F8")
        for row in t.rows:
            for i, cell in enumerate(row.cells):
                if widths:
                    cell.width = Inches(widths[i])
                for para in cell.paragraphs:
                    para.paragraph_format.line_spacing = 1.0
                    para.paragraph_format.space_after = Pt(0)
                    para.paragraph_format.space_before = Pt(0)
        after = self.doc.add_paragraph()
        after.paragraph_format.space_after = Pt(4)
        after.paragraph_format.line_spacing = 1.0
        if note:
            add_rich(after, f"*Note:* {note}", size=8.5, color=GREY)
        else:
            set_run_font(after.add_run(""), size=4)
        return n


# ----------------------------------------------------------------------------- document set-up
def setup_styles(doc):
    # The cover template stores built-in names capitalised ("Heading 1"); Word and python-docx expect "heading 1".
    for st in doc.styles:
        raw = st.element.name_val
        if raw and (re.fullmatch(r"Heading [1-9]", raw) or raw in ("Caption", "Header", "Footer")):
            st.element.name_val = raw.lower()
            if raw.startswith("Heading"):
                ppr = st.element.get_or_add_pPr()
                if ppr.find(qn("w:outlineLvl")) is None:
                    lvl = OxmlElement("w:outlineLvl")
                    lvl.set(qn("w:val"), str(int(raw[-1]) - 1))
                    ppr.append(lvl)
    for name, size in (("Normal", 12), ("Heading 1", 15), ("Heading 2", 13), ("Caption", 10), ("toc 1", 11),
                       ("toc 2", 10.5), ("table of figures", 10)):
        st = doc.styles[name]
        st.font.name = FONT
        st.font.size = Pt(size)
        rpr = st.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.insert(0, fonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(attr), FONT)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            fonts.attrib.pop(qn(attr), None)
        if name.startswith("Heading"):
            st.font.color.rgb = NAVY
            st.font.bold = True
        if name.startswith("toc") or name == "table of figures":
            st.paragraph_format.space_after = Pt(1)
            st.paragraph_format.line_spacing = 1.0


def fill_cover(doc):
    paras = doc.paragraphs

    def put(par, texts):
        for i, run in enumerate(par.runs):
            run.text = texts[i] if i < len(texts) else ""

    put(paras[1], ["VIRAL PRODUCT INTELLIGENCE:",
                   "\nEarly Detection and Predictive Modeling of Consumer Demand Surges Using Multi-Source Signals"])
    put(paras[3], ["Pro", "ject Team: Group 13"])
    (n1, r1, w1), (n2, r2, w2), (n3, r3, w3), (n4, r4, w4), (n5, r5, w5) = TEAM
    put(paras[4], [f"{n1} ({r1}) — {w1}", f"\n{n2} ({r2}) — {w2}"])
    put(paras[5], [f"{n3} (", r3, f") — {w3}"])
    put(paras[6], [n4, " (", r4, ") —", " ", w4])
    put(paras[7], [f"{n5} (", r5, f") — {w5}", ""])
    for run in paras[7].runs:
        run.italic = False
    paras[7].paragraph_format.space_before = paras[6].paragraph_format.space_before
    paras[8].paragraph_format.space_before = Pt(18)


def page_numbers(section, fmt, start=1, header=None):
    section.footer.is_linked_to_previous = False
    section.header.is_linked_to_previous = False
    para = section.footer.paragraphs[0]
    para.text = ""
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(add_field(para, "PAGE", "1"), size=10)
    pg = section._sectPr.find(qn("w:pgNumType"))
    if pg is None:
        pg = OxmlElement("w:pgNumType")
        section._sectPr.append(pg)
    pg.set(qn("w:fmt"), fmt)
    pg.set(qn("w:start"), str(start))
    if header:
        hp = section.header.paragraphs[0]
        hp.text = ""
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        add_rich(hp, header, size=9, color=GREY)


def f3(x):
    return f"{x:.3f}"


# ----------------------------------------------------------------------------- content
def build():
    doc = Document(str(COVER))
    setup_styles(doc)
    fill_cover(doc)

    m1 = pd.read_csv(ROOT / "processed" / "method1_model_comparison.csv")
    m1c = pd.read_csv(ROOT / "processed" / "method1_cohort_performance.csv").set_index("cohort")
    u2 = pd.read_csv(OUT / "unit2_model_comparison.csv")
    u2cv = pd.read_csv(OUT / "unit2_cv_results.csv")
    u2c = pd.read_csv(OUT / "unit2_cohort_performance.csv")
    u2fi = pd.read_csv(OUT / "unit2_feature_importance.csv")
    u2cm = pd.read_csv(OUT / "unit2_confusion_matrix.csv", index_col=0)
    te = pd.read_csv(ROOT / "data" / "processed" / "text_eval_method_comparison.csv")
    te = te[te.reference.str.startswith("all")]
    audit = pd.read_csv(ROOT / "processed" / "method2_leakage_audit_report.csv")
    mval = pd.read_csv(ROOT / "processed" / "master_validation_report.csv")
    pipe = pd.read_csv(ROOT / "processed" / "pipeline_execution_summary.csv")
    scen = pd.read_csv(OUT / "scenario_summary.csv")
    master = pd.read_csv(ROOT / "processed" / "master_dataset.csv")

    rf = u2[u2.model == "Random Forest"].iloc[0]
    lr = u2[u2.model.str.startswith("Logistic")].iloc[0]
    lgbm = m1[m1.model_name == "LightGBM"].iloc[0]
    naive = m1[m1.model_name == "Naive_Persistence"].iloc[0]
    arima = m1[m1.model_name == "ARIMA(1,1,1)"].iloc[0]
    mae_red = (naive.test_mae - lgbm.test_mae) / naive.test_mae * 100
    rmse_red = (naive.test_rmse - lgbm.test_rmse) / naive.test_rmse * 100
    arima_red = (arima.test_mae - lgbm.test_mae) / arima.test_mae * 100
    prev = 208 / 3180
    lift = rf.pr_auc / prev
    vader = te[te.method.str.startswith("VADER")].iloc[0]
    tblob = te[te.method == "TextBlob"].iloc[0]
    alwayspos = te[te.method.str.startswith("Always")].iloc[0]
    base = scen[scen.scenario == "Base"].iloc[0]
    fp = int(u2cm.loc["actual_no_surge", "pred_surge"])
    fn = int(u2cm.loc["actual_surge", "pred_no_surge"])
    tp = int(u2cm.loc["actual_surge", "pred_surge"])
    cv_rf = u2cv[u2cv.model == "Random Forest"].cv_pr_auc_mean.max()
    mpass = int((mval.status == "PASS").sum())

    # ------------------------------------------------------------------ front matter
    front = doc.add_section(WD_SECTION.NEW_PAGE)
    page_numbers(front, "lowerRoman")
    R = Report(doc)

    R.h1("Declaration", in_toc=True)
    R.p("We, the members of Group 13, declare that this report, **“Viral Product Intelligence: Early Detection and "
        "Predictive Modeling of Consumer Demand Surges Using Multi-Source Signals”**, submitted for 23CSE452 Business "
        "Analytics, is our original work. The data were collected by the team from Google Trends and the YouTube "
        "Data API v3; no pre-built repository dataset was used. Sources are cited, generative-AI use is disclosed in "
        "Section 8.3, and the work has not been submitted for any other course or award.", spacing=1.3)
    R.table(["Name", "Register No.", "Signature"], [[n, r, ""] for n, r, _ in TEAM], "Declaration signatories",
            widths=[2.4, 1.6, 2.4], size=10)
    R.p("Place: Coimbatore          Date: November 2026", align=WD_ALIGN_PARAGRAPH.LEFT, spacing=1.0)
    R.h1("Acknowledgement")
    R.p("We thank our project guide and the faculty of the Department of Computer Science and Engineering, Amrita "
        "School of Computing, Amrita Vishwa Vidyapeetham, Coimbatore, for their guidance across Reviews 1 and 2, and "
        "the open-source communities behind pandas, scikit-learn, statsmodels, LightGBM, vaderSentiment and "
        "Streamlit.", spacing=1.3)

    R.h1("Abstract", page_break=True)
    R.p("Retailers in India's smartphone market usually see viral demand only after it reaches sales data. This "
        "project tests whether public digital signals can flag demand surges earlier. We collected an original "
        "dataset for 60 smartphones (13 brands; 24 RISING, 24 STABLE, 12 DECLINING): 269 days of daily Google Trends "
        "search interest for India (8 Jan–3 Oct 2026) and YouTube Data API snapshots of 3,149 review videos and "
        "29,189 unique comments, consolidated into a validated master dataset of 16,140 product-days × 43 variables. "
        "Unit 1 analysis shows strong short-lag persistence and cohort-specific dynamics. Unit 2 builds leak-free "
        f"lag features and a next-day surge classifier; the selected Random Forest reaches a held-out PR-AUC of "
        f"{rf.pr_auc:.3f} versus {prev:.3f} for chance. Unit 3 Method 1 (time-series forecasting) benchmarks ten "
        f"models on a 53-day chronological test window; LightGBM reaches MAE {lgbm.test_mae:.2f} index points "
        f"(R² {lgbm.test_r2:.3f}), {mae_red:.1f}% below naive persistence. Unit 3 Method 2 (text mining) applies "
        "VADER sentiment, LDA/NMF topics and lead-lag tests; comment activity moves with search in the same week but "
        "does not lead it. Results are delivered through a six-page Streamlit dashboard backed by a ten-stage "
        "orchestrated pipeline and 222 passing tests.")
    R.p("**Keywords:** demand sensing, Google Trends, YouTube, time-series forecasting, text mining, surge detection.",
        align=WD_ALIGN_PARAGRAPH.LEFT)
    R.table(["Abbreviation", "Meaning"], [
        ["ARIMA / ETS", "Autoregressive integrated moving average / exponential smoothing"],
        ["CV", "Cross-validation"], ["EDA", "Exploratory data analysis"], ["KPI", "Key performance indicator"],
        ["LDA / NMF", "Latent Dirichlet allocation / non-negative matrix factorisation"],
        ["MAE / RMSE / WAPE", "Mean absolute / root mean squared / weighted absolute percentage error"],
        ["MDA", "Mean directional accuracy"],
        ["PR-AUC / ROC-AUC", "Area under the precision–recall / ROC curve"],
        ["SI", "Google Trends search interest (0–100)"],
        ["VADER", "Valence Aware Dictionary and sEntiment Reasoner"],
    ], "Abbreviations", widths=[1.6, 4.8], size=9.5, bold_first_col=True)

    R.h1("Executive Summary", page_break=True)
    R.p("**Problem.** Smartphone demand in India can spike within days of launches, reviews or price news. Planners "
        "who wait for sales reports react late: stock-outs on surging models, excess stock on fading ones.")
    R.p("**What we built.** A pipeline that tracks 60 phones daily using two free public signals—Google searches "
        "and YouTube reviews and comments—and turns them into forecasts, next-day surge alerts and a dashboard.")
    R.bullets([
        "**Data:** 16,140 validated product-days, no gaps or duplicates; 806 surge days (+50% day-on-day search jump).",
        f"**Forecasts:** average error {lgbm.test_mae:.1f} points on a 0–100 scale—{mae_red:.1f}% better than "
        f"“tomorrow equals today” and {arima_red:.1f}% better than ARIMA.",
        f"**Surge alerts:** {lift:.1f}× better than chance at ranking surge days; {int(rf.alerts)} alerts in 53 days "
        f"catch {rf.recall * 100:.0f}% of surges.",
        "**Audience voice:** 58% of comments positive, 12% negative; comment tone does not predict next week's demand.",
    ])
    R.p(f"**Business value.** Both KPI targets were met. Under illustrative base-case costs (₹25,000 protected per "
        f"anticipated surge-day, ₹4,000 per alert) alerts net ₹{base.net_inr / 1e5:.1f} lakh over 53 days; they lose "
        "money if an alert costs more than ~23% of a surge-day's value, so thresholds must match each retailer's costs.")
    R.p("**Actions.** Use forecasts for short-horizon allocation; route RISING-model alerts to category managers as a "
        "daily check list; use sentiment for brand health, not demand; pilot with one retailer and add sales data.")

    R.h1("Table of Contents", page_break=True, in_toc=False)
    add_field(doc.add_paragraph(), 'TOC \\o "1-2" \\h \\z \\u', "Update field to build the table of contents.")
    R.h1("List of Figures and Tables", in_toc=False)
    add_field(doc.add_paragraph(), 'TOC \\h \\z \\c "Figure"', "Update field to build the list of figures.")
    add_field(doc.add_paragraph(), 'TOC \\h \\z \\c "Table"', "Update field to build the list of tables.")

    # ------------------------------------------------------------------ main body
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    page_numbers(body, "decimal", 1, "Viral Product Intelligence  |  Group 13  |  23CSE452 Business Analytics")

    # 1
    R.h1("1. Introduction, Business Objectives and Literature Review")
    R.h2("1.1 Context and scope")
    R.p("India's smartphone market has frequent launches, short life cycles and strong reviewer influence. Demand "
        "for a model can surge within days and fade when a successor arrives. Retail category managers, brand "
        "marketers and distributors all need earlier warning than sales data give. We use public *leading* signals—"
        "Google search interest (purchase intent) and YouTube reviews and comments (awareness and opinion)—for 60 "
        "phones sold in India, 8 Jan–3 Oct 2026. Unit sales and prices are out of scope (no public, terms-compliant "
        "source), so search interest serves as the demand proxy. Questions: **Q1** how signals differ by cohort and "
        "brand; **Q2** can next-day surges be flagged; **Q3** how accurately can next-day interest be forecast; "
        "**Q4** what audiences say and whether it leads demand; **Q5** can results be delivered as a usable tool.")
    t_kpi = R.table(["Objective", "KPI / unit", "Baseline / target", "Unit"], [
        ["O1 Validated daily dataset", "Product-day coverage; checks passed", "Target 100% of 60×269; 0 failures", "U1"],
        ["O2 Flag next-day surges", "PR-AUC (held-out)", f"Chance {prev:.3f} / target ≥ 2× chance", "U2"],
        ["O3 Forecast next-day interest", "MAE (index points)", f"Naive {naive.test_mae:.2f} / target ≥ 5% lower",
         "U3 M1"],
        ["O4 Measure opinion and lead time", "Accuracy vs reference labels; lead-lag ρ",
         f"Always-positive {alwayspos.accuracy:.3f} / beat it; test positive lag", "U3 M2"],
        ["O5 Decision support", "Working pages and filters; tests", "All units in one app; 100% tests pass", "App"],
    ], "Business objectives, KPIs and success criteria", widths=[1.9, 1.7, 2.3, 0.6], size=9)
    f_flow = R.figure(OUT / "fig_workflow.png", "End-to-end workflow linking Units 1–3 and the application.",
                      "reports/make_report_figures.py", width=6.3)
    R.h2("1.2 Related work and research gap")
    R.p("Choi and Varian [1] showed that adding Google Trends to seasonal autoregressive models improves nowcasts of "
        "US retail and travel series by roughly 5–20%, using aggregated weekly or monthly data. Asur and Huberman [2] "
        "predicted box-office revenue from tweet rates (adjusted R² ≈ 0.80), a single-launch setting. Boone et al. "
        "[3] reviewed consumer analytics in sales forecasting and found benefits mainly for new products and short "
        "horizons. Carbonneau et al. [4] found machine-learning demand forecasts best but not significantly better "
        "than regression. VADER [5] reached F1 ≈ 0.96 on English tweets. **Gap:** prior work uses aggregates, one "
        "signal at a time, English-only text and rarely documents leakage controls. We therefore build a daily, "
        "product-level, multi-source panel for Indian phones, evaluate chronologically against naive baselines, "
        "validate sentiment on local code-mixed comments and audit leakage.")

    # 2
    R.h1("2. Dataset Collection, Validation and Preparation")
    R.h2("2.1 Provenance, coverage and challenges")
    R.table(["Source", "Method", "Dates", "Volume"], [
        ["Google Trends (India, web)", "pytrends with polite pauses/back-off; loader validates query, range, "
         "daily granularity", "8 Jan–3 Oct 2026", "60 series, 16,140 rows"],
        ["YouTube Data API v3", "search (top 50, region IN), video statistics, top 100 comments on 5 most-viewed "
         "videos", "Snapshots 3–4 Oct 2026", "3,149 videos; 29,189 comments"],
        ["Product registry v3", "Team-curated: queries, brand, launch period, cohort", "3 Oct 2026",
         "60 phones, 13 brands"],
    ], "Data sources (public data under Google's terms; team-collected)", widths=[1.4, 2.8, 1.2, 1.1], size=9)
    R.p("The master dataset `processed/master_dataset.csv` has **16,140 rows × 43 variables**, one row per "
        "product-day. Targets: `surge_label` (1 if day-on-day growth ≥ +50%, the 85th percentile of positive "
        "growth) for Unit 2 and `search_interest` for Method 1.")
    sample = pd.concat([master[master.surge_label == 1].sample(3, random_state=7),
                        master[(master.surge_label == 0) & (master.search_interest > 0)].sample(3, random_state=7)]
                       ).sort_values(["date", "product_id"])
    R.table(["ID", "Date", "Brand", "Cohort", "SI", "Growth", "Videos", "Comments", "Surge"],
            [[r.product_id, r.date, r.brand, r.product_type, f"{r.search_interest:.0f}",
              "" if pd.isna(r.search_interest_growth) else f"{r.search_interest_growth:+.2f}",
              int(r.videos_published), int(r.comments_published), int(r.surge_label)] for r in sample.itertuples()],
            "Sample rows of the final dataset (selected columns)", size=8.5,
            widths=[0.45, 0.95, 0.8, 0.95, 0.4, 0.6, 0.6, 0.75, 0.5])
    R.p("**Challenges.** YouTube quotas (10,000 units, 100 searches/day) were handled by a quota tracker, caching "
        "and a measured cost of 7 units per product; Trends rate limits by back-off; per-product Trends scaling by "
        "using within-product changes; sibling-model results (e.g., “Pro” variants) by an on-target title matcher "
        "that flags products below 60%; and snapshot timing by rebuilding daily activity from publish timestamps.")
    R.h2("2.2 Data quality, privacy and ethics")
    R.p(f"The builder writes the master file only if all checks pass ({mpass} PASS, 0 FAIL): 60 products × 269 "
        "days with no gaps; 0 duplicate keys; interest within 0–100; no negative counts; growth left missing on "
        "3,688 undefined rows (never set to 0); 15 low-signal and 4 low on-target products flagged, not dropped; "
        "1,100 rows from superseded queries excluded with logged reasons; surge class imbalance 4.99%. "
        "**Privacy:** only public content is used; commenter names and channel IDs are stripped, raw API files are "
        "git-ignored, the API key stays in a local `.env`, and use is limited to this research purpose, consistent "
        "with India's DPDP Act, 2023 [12]. **Biases:** relevance-ranked, English-leaning YouTube samples; October "
        "comment snapshots (survival bias); Trends sampling and truncation; more Samsung/Apple models.")
    R.h2("2.3 Transformations and usable dataset")
    R.p("General cleaning kept active query versions (comments 33,217 → 32,215), standardised timestamps, removed "
        "duplicates (→ 29,189 unique comments), kept outliers and flagged rather than dropped short, emoji-only and "
        "non-Latin comments (22,568 usable). Trends and YouTube were left-joined on the daily index without forward "
        "fill. **Model-specific** steps after the chronological split (cut-off 12 Aug 2026): lags t−1, 2, 3, 7; "
        "7-day rolling mean/SD with `shift(1)`; one-hot brand/cohort; scaler and topic models fitted on training "
        "data only. After a 7-day burn-in: 12,540 train and 3,180 test rows.")
    R.table(["Field group", "Meaning / preparation", "Used in"], [
        ["search_interest; growth; accel", "Daily Trends index and its changes", "U1, U2, M1 target"],
        ["surge_label", "0/1, growth ≥ +50% (identical with a train-only cut-off)", "U2 target"],
        ["*_lag_1/2/3/7, rolling_mean/std_7d", "Lagged search, growth, accel, videos, comments", "U2, M1"],
        ["videos_published, comments_published", "Daily counts from YouTube publish timestamps", "U1, U2, M1, M2"],
        ["brand_*, product_type_*", "One-hot dummies", "U1, U2, M1"],
        ["text_clean, vader_compound, topic shares", "Cleaned comments, sentiment, LDA/NMF topics", "M2"],
        ["usable_product, low_on_target_share", "Quality flags", "U1 bias checks"],
    ], "Mapping of final fields to Units 1–3", widths=[2.3, 3.0, 1.2], size=9)

    # 3
    R.h1("3. Exploratory Data Analysis and Visualization (Unit 1)")
    g = master.groupby("product_type").agg(n=("product_id", "nunique"), mean=("search_interest", "mean"),
                                           median=("search_interest", "median"), zero=("is_zero", "mean"),
                                           surge=("surge_label", "mean"), comments=("comments_published", "sum"))
    t_eda = R.table(["Cohort", "Products", "Mean SI", "Median SI", "Zero days", "Surge rate", "Comments"],
                    [[c, int(r.n), f"{r['mean']:.1f}", f"{r['median']:.0f}", f"{r.zero * 100:.1f}%",
                      f"{r.surge * 100:.1f}%", int(r.comments)] for c, r in g.loc[["RISING", "STABLE",
                                                                                    "DECLINING"]].iterrows()],
                    "Descriptive statistics by cohort", widths=[1.0, 0.8, 0.8, 0.9, 0.9, 0.9, 0.9], size=9)
    f_eda = R.figure(FIG / "eda_full_03_mean_interest_by_type.png", "Mean daily search interest by cohort (7-day "
                     "moving averages).", "notebooks/04_eda_full.ipynb", width=5.2,
                     note="RISING models sit near zero until launch season and climb from late June (49% zero "
                          "days); STABLE and DECLINING models fluctuate around 25–35 with short spikes, e.g., the "
                          "mid-September flagship launches. Cohort therefore drives surge rates and forecast "
                          "difficulty.")
    f_lag = R.figure(FIG / "ts_lag_relationships.png", "Autocorrelation of search interest and its correlation "
                     "with the surge label, averaged over 60 products.", "src/prep_time_series.py", width=4.8,
                     note="Interest is highly persistent (autocorrelation 0.61 at 1 day, 0.25 at 14 days), so naive "
                          "persistence is a strong baseline; yet lagged *levels* barely correlate with surges "
                          "(|r| < 0.03). This motivated short lags, rolling volatility and non-linear models.")
    R.p("Other findings: interest correlates only weakly with comments (Spearman 0.20) and videos (0.10), so social "
        "signals are complementary; weekly seasonality is mild (Monday 18.4 to Sunday 20.8); 95% of series are "
        "stationary after differencing, supporting ARIMA(1,1,1) as a benchmark. **Hypotheses carried forward:** "
        "recent momentum predicts level and surges; cohort shifts baselines; comments may lead search.")

    # 4
    R.h1("4. Feature Engineering and Predictive Modelling (Unit 2)")
    R.p("**Task.** Using information up to day t−1, predict whether day t is a surge. **Features (36):** 20 lags, "
        "2 leak-free rolling statistics and 14 brand/cohort dummies; no dimensionality reduction (small, "
        "interpretable set). **Protocol:** chronological split (train 586 surges, 4.67%; test 208, 6.54%); 5-fold "
        "expanding-window CV by date on training data to choose settings by PR-AUC; decision threshold set to "
        "maximise out-of-fold F1; test scored once. PR-AUC is primary because surges are rare [15].")
    t_u2 = R.table(["Model", "Settings", "CV PR-AUC", "Precision", "Recall", "F1", "ROC-AUC", "PR-AUC"],
                   [[("**" + r.model + "**") if r.model == "Random Forest" else r.model,
                     {"Persistence rule (baseline)": "growth_lag_1 rule",
                      "Logistic Regression (baseline)": "L2, C=0.1, balanced",
                      "Random Forest": "300 trees, depth 10",
                      "HistGradientBoosting": "lr 0.05, depth 4"}[r.model],
                     "–" if r.model.startswith("Persistence") else
                     f3(u2cv[u2cv.model == r.model].cv_pr_auc_mean.max()),
                     f3(r.precision), f3(r.recall), f3(r.f1), f3(r.roc_auc), f3(r.pr_auc)] for r in u2.itertuples()],
                   "Unit 2 surge classification on the held-out window (3,180 rows, 208 surges)",
                   widths=[1.5, 1.3, 0.6, 0.6, 0.55, 0.45, 0.6, 0.6], size=8.5,
                   note=f"Chance PR-AUC = {prev:.3f}. Script: reports/unit2_surge_classification.py.")
    R.p(f"Random Forest had the best CV PR-AUC ({cv_rf:.3f}) and was fixed before testing. On test it reaches "
        f"PR-AUC {rf.pr_auc:.3f} ({lift:.1f}× chance) and ROC-AUC {rf.roc_auc:.3f}, double logistic regression "
        f"({lr.pr_auc:.3f}), confirming non-linear structure. It catches {tp} of 208 surges with {fp} false alarms "
        f"and {fn} misses; precision ≈ 23% makes it a triage tool, not an auto-reorder rule. **Leakage audit:** all "
        "features lagged; rolling windows exclude day t; scaler fit on train; split by date; the surge cut-off "
        "recomputed on training data alone is identical (all 16,140 labels unchanged); Unit 1/Unit 2 label parity "
        "100%.")
    f_u2 = R.figure(OUT / "unit2_classification.png", "Precision–recall curves on the test window (left) and "
                    "permutation importance of the Random Forest (right).", "reports/unit2_surge_classification.py",
                    width=6.0,
                    note=f"Tree ensembles dominate logistic regression at all recalls. Yesterday's growth "
                         f"({u2fi.iloc[0].pr_auc_drop_mean:.3f} PR-AUC drop) and level "
                         f"({u2fi.iloc[1].pr_auc_drop_mean:.3f}), then 7-day volatility, matter most; YouTube lags "
                         "add little. Importances are associations, not causes.")
    rc = u2c.set_index("cohort")
    R.p(f"**Subgroup check.** ROC-AUC is similar for RISING ({rc.loc['RISING'].roc_auc:.2f}) and STABLE "
        f"({rc.loc['STABLE'].roc_auc:.2f}) but lower for DECLINING ({rc.loc['DECLINING'].roc_auc:.2f}), where rarer "
        f"surges follow successor launches the lags cannot see; recall is highest for RISING "
        f"({rc.loc['RISING'].recall * 100:.0f}%), where early warning matters most. Cohort-specific thresholds and "
        "launch-date features are the proposed mitigation.")

    # 5
    R.h1("5. Advanced Analytics (Unit 3): Two Selected Methods")
    R.p("We selected **Time-Series Analysis** and **Text Mining**; association rules do not apply (no baskets).")
    R.h2("5.1 Method 1 — Time-series forecasting of daily search interest")
    R.p("**Question (Q3):** how accurately can tomorrow's interest be predicted for allocation decisions? **Input:** "
        "12,540 train and 3,180 test product-days (12 Aug–3 Oct, 53 days). **Models:** naive (ŷₜ = yₜ₋₁), seasonal "
        "naive (yₜ₋₇), 7-day mean; per-product ARIMA(1,1,1) and Holt smoothing; Ridge, ElasticNet, Random Forest, "
        "Gradient Boosting and LightGBM [7] (100 trees, depth 4, learning rate 0.05) on the 36 features; one-step "
        "forecasts clipped to 0–100; metrics per [14]. Code: `src/prep_time_series.py`, `src/forecast_method1.py`, "
        "`notebooks/scrum26_time_series_forecasting.ipynb`.")
    t_m1 = R.table(["Model", "Family", "MAE", "RMSE", "WAPE %", "R²", "MDA %"],
                   [[("**LightGBM**" if r.model_name == "LightGBM" else r.model_name.replace("_", " ")),
                     r.model_family.replace("_", " "), f"{r.test_mae:.3f}", f"{r.test_rmse:.3f}",
                     f"{r.test_wape_pct:.2f}", f"{r.test_r2:.3f}", f"{r.test_mda_pct:.1f}"] for r in m1.itertuples()],
                   "Method 1 benchmark on the 53-day test window (3,180 product-days)",
                   widths=[1.7, 1.3, 0.65, 0.65, 0.7, 0.6, 0.65], size=8.5)
    f_m1 = R.figure(FIG / "method1_forecast_trajectories.png", "Forecast versus actual search interest for "
                    "representative products in the test window.", "src/forecast_method1.py", width=5.3,
                    note="Forecasts follow level shifts within a day but lag the first day of sharp spikes—the "
                         "reason a separate surge classifier is needed.")
    R.p(f"**Findings.** All ML models beat every baseline; LightGBM is best: MAE {lgbm.test_mae:.3f} "
        f"({mae_red:.1f}% below naive, {arima_red:.1f}% below ARIMA) and RMSE {rmse_red:.1f}% lower than naive. Gains "
        f"are largest for STABLE ({m1c.loc['STABLE'].mae_reduction_vs_naive_pct:.1f}%, R² "
        f"{m1c.loc['STABLE'].best_model_r2:.2f}) and DECLINING ({m1c.loc['DECLINING'].mae_reduction_vs_naive_pct:.1f}%) "
        f"and small for RISING ({m1c.loc['RISING'].mae_reduction_vs_naive_pct:.1f}%). `search_lag_1` and the 7-day "
        "mean carry ~95% of tree importance. **Limitations:** MDA ≈ 50% for all models (no directional skill); fixed "
        "hyperparameters; one 53-day window; without LightGBM installed the script substitutes Extra Trees.")
    R.h2("5.2 Method 2 — Text mining of YouTube comments")
    R.p("**Question (Q4):** what do audiences say, and does comment activity or tone lead search? **Input:** 29,189 "
        "comment rows (22,568 usable). **Steps:** cleaning, domain stop-list (platform words, Hinglish fillers) and "
        "removal of brand tokens; VADER [5] compound score (positive ≥ 0.05, negative ≤ −0.05); LDA [6] and NMF "
        "[11] for k = 3–10 chosen by NPMI coherence and refit stability; validation on 100 random comments "
        "(5 human, 95 AI-review labels, flagged per row) against TextBlob and an always-positive baseline; weekly "
        "Spearman lead-lag of comment and sentiment changes vs search change. Code: `src/text_mining.py`, "
        "`src/evaluate_text_mining.py`, `notebooks/05_text_mining.ipynb`.")
    f_top = R.figure(OUT / "fig_topics.png", "Top-10 terms of the three LDA topics (top) and NMF comparison "
                     "(bottom).", "reports/make_report_figures.py from data/processed/text_topics_terms.csv",
                     width=5.6,
                     note="Topics: *Buying advice* (price, AI, Hinglish “chahiye”, “lena”; 30%), *Upgrading & "
                          "ownership* (camera, battery, years; 33%) and *Design & display* (love, screen, looks; "
                          "37%, 45% for DECLINING models). NMF recovers the camera/price and design themes. "
                          "Coherence is low (LDA 0.021, NMF 0.085) because comments are short (median 5 tokens).")
    t_m2 = R.table(["Method (93 labelled comments)", "Accuracy [95% CI]", "Macro-F1", "Neg. recall", "κ"],
                   [[("**VADER (project)**" if r.method.startswith("VADER") else r.method),
                     f"{r.accuracy:.3f} [{r.accuracy_ci95_low:.2f}–{r.accuracy_ci95_high:.2f}]", f3(r.macro_f1),
                     f3(r.negative_recall), f"{r.cohen_kappa:.2f}"] for r in te.itertuples()],
                   "Sentiment validation against reference labels", widths=[2.4, 1.5, 0.8, 0.8, 0.5], size=8.5,
                   note="The AI-review row is not independent: it supplied most reference labels.")
    f_ll = R.figure(FIG / "scrum29_lead_lag.png", "Weekly lead-lag correlation of comment signals with search "
                    "change (* p < 0.05).", "src/evaluate_text_mining.py", width=5.4,
                    note="Comment volume rises in the *same* week as search (ρ = 0.13, p < 0.001) but not a week "
                         "earlier (ρ = 0.03, p = 0.12); sentiment two weeks earlier is weakly *negative* "
                         "(ρ = −0.12, p = 0.006). Comments are a coincident, not leading, signal.")
    R.p(f"**Findings.** 58.4% of scored comments are positive, 11.5% negative; Oppo (0.39) and iQOO/Realme (0.34) "
        f"audiences are most positive, Motorola and Nothing least (0.27). VADER beats TextBlob and the baseline "
        f"(accuracy {vader.accuracy:.3f} vs {tblob.accuracy:.3f} and {alwayspos.accuracy:.3f}) but misses most "
        f"negatives (recall {vader.negative_recall:.2f}) due to Hinglish, sarcasm and jargon. Method 2 model inputs "
        f"passed {int((audit.status == 'PASS').sum())} of {len(audit)} leakage checks (one informational note on "
        "October-snapshot survival bias).")

    # 6
    R.h1("6. Comparative Analysis and Business Impact")
    R.h2("6.1 Comparison of the two Unit 3 methods")
    R.table(["Criterion", "Method 1: Time-series", "Method 2: Text mining"], [
        ["Question / input", "Next-day level; numeric daily panel", "Opinion and lead time; 29,189 comments"],
        ["Output / validation", "Point forecasts; chronological hold-out vs 9 alternatives",
         "Sentiment, topics, ρ; reference labels, coherence, significance"],
        ["Strengths", "Clear gains over naive and ARIMA; reproducible", "Explains *why*; brand-level insight"],
        ["Limitations", "No directional skill; lags spike onsets", "English lexicon; short text; not leading"],
    ], "Comparison of the two Unit 3 methods", widths=[1.3, 2.6, 2.6], size=9, bold_first_col=True)
    R.p("The metrics are not comparable (MAE vs accuracy), so neither method is “better”. **Synthesis:** Units 1, 2 "
        "and Method 1 agree that recent momentum dominates (high autocorrelation, top importance of lagged growth "
        "and level); YouTube activity is consistently secondary and, per Method 2, coincident. The Review 1 "
        "hypothesis that sentiment leads demand by 3–7 days is not supported, so text serves as a brand-health "
        "layer while numeric momentum drives alerts. The dashboard's Viral Radar combines both as a descriptive "
        "0–100 score (weights 0.40 level, 0.35 acceleration, 0.25 sentiment), not a validated predictor.")
    R.h2("6.2 Comparison with existing studies")
    R.table(["Criterion", "Choi & Varian [1]", "Asur & Huberman [2]", "Hutto & Gilbert [5]", "This project"], [
        ["Data", "US retail/travel; weekly–monthly Trends", "Box office; tweet rate", "English tweets",
         "60 Indian phones; daily Trends + YouTube"],
        ["Validation", "Rolling out-of-sample", "In-sample R²", "F1 vs human labels",
         "Chronological hold-out, CV, leakage audit"],
        ["Finding", "Error −5 to −20% vs AR", "Adj. R² ≈ 0.80", "F1 ≈ 0.96",
         f"MAE −{mae_red:.1f}% vs naive, −{arima_red:.1f}% vs ARIMA; VADER F1 {vader.macro_f1:.2f}"],
    ], "Comparison with prior studies", widths=[0.8, 1.35, 1.25, 1.2, 1.9], size=8.5, bold_first_col=True)
    R.p("Direct comparison is limited: Choi and Varian measure the gain of *adding* Trends to forecast sales, while "
        "our target is the Trends index; Asur and Huberman report in-sample R² for single launches; VADER's 0.96 is "
        "binary English sentiment, whereas our 0.47 is three-class, on Hinglish-heavy comments with mostly AI "
        "reference labels—largely domain shift. Our contribution is a reproducible daily product-level panel for an "
        "emerging market, explicit leakage controls, cohort diagnostics and an honest negative lead-lag result.")
    R.h2("6.3 KPI-based business impact and scenarios")
    R.p(f"**Measured:** forecast MAE −{mae_red:.1f}% (O3 met); surge PR-AUC {lift:.1f}× chance with {tp} true alerts "
        f"of {int(rf.alerts)} (O2 met). **Hypothetical economics:** net = true alerts × value per anticipated "
        "surge-day − alerts × cost per alert.")
    t_sc = R.table(["Scenario", "Value / surge-day (₹)", "Cost / alert (₹)", "Benefit (₹ lakh)", "Cost (₹ lakh)",
                    "Net (₹ lakh)"],
                   [[r.scenario, f"{r.value_per_anticipated_surge_day_inr:,}", f"{r.cost_per_alert_inr:,}",
                     f"{r.benefit_inr / 1e5:.2f}", f"{r.cost_inr / 1e5:.2f}", f"**{r.net_inr / 1e5:+.2f}**"]
                    for r in scen.itertuples()],
                   "Best/base/worst-case net impact over the 53-day test window", size=9,
                   widths=[0.8, 1.3, 1.1, 1.1, 1.0, 1.0], note="Illustrative assumptions; replace with partner data.")
    f_sc = R.figure(OUT / "fig_scenarios.png", "Net impact versus alert threshold for the three scenarios.",
                    "reports/make_report_figures.py", width=5.2,
                    note=f"Base case nets ₹{base.net_inr / 1e5:.1f} lakh and stays positive above a threshold of "
                         f"~0.33; the worst case loses at almost every threshold. Break-even needs surge-day value "
                         f"> {int(rf.alerts) / tp:.1f}× alert cost; the key dependencies are stock-out margin, "
                         "action cost and search-to-sales conversion.")

    # 7
    R.h1("7. Interactive Application, Figure Index and Reproducibility")
    R.h2("7.1 Dashboard")
    R.p("A local multi-page Streamlit [13] app: `pip install -r requirements.txt`, then `streamlit run "
        "dashboard/app.py` (or `python -m streamlit run dashboard/app.py`). `data_loader.py` caches the committed "
        "outputs; sidebar filters (category, brand, cohort, products, date range, reset) persist across pages via "
        "`filters.py`. Pages: Overview (U1 KPIs), Viral Radar (U2+U3 synthesis), Search Trends (surge markers, "
        "calendar), Forecasts (M1, errors recomputed per selection), Audience Signals and Text Mining (M2). "
        "Filtering, KPIs and the radar score are computed live; models and scores are precomputed.")
    f_d1 = R.figure(DASH / "05_overview_filtered.png", "Overview page filtered to Samsung / RISING (4 products, 33 "
                    "surge days); every KPI and chart updates.", "running Streamlit app", width=5.6)
    f_d2 = R.figure(DASH / "02_radar_all.png", "Viral Radar page: demand acceleration versus sentiment with "
                    "breakout-score KPIs.", "running Streamlit app", width=5.6)
    R.p("**Tests and limitations.** `tests/test_dashboard.py` covers filter logic and headless page renders (all "
        "pass). Forecast headline cards show the global benchmark; radar weights are not back-tested; the app is not "
        "publicly hosted; data end on 3 Oct 2026.")
    R.h2("7.2 Figure index and reproduction map")
    R.table(["Fig.", "Title", "Section", "Source file"],
            [[n, c.rstrip(".")[:75], s.rstrip("."), src.replace("`", "")[:55]] for n, c, s, src in R.figures],
            "Figure index (all figures created in Python/Matplotlib or captured from the app)",
            widths=[0.4, 3.3, 0.6, 2.2], size=8)
    R.h2("7.3 Code and environment")
    R.p(f"Repository: {REPO} (public README with tree and commands). Python 3.12; dependencies in "
        "`requirements.txt`. Layout: `raw/`, `data/processed/`, `processed/`, `src/`, `notebooks/`, `docs/figures/`, "
        "`dashboard/`, `tests/`, `reports/`. Reproduce: `python -m pytest`; `python src/run_full_pipeline.py` "
        "(Units 1–3); `python reports/unit2_surge_classification.py`. Raw YouTube JSON (with commenter names) and "
        "the API key are excluded; both sources can be re-collected with a personal key under Google's terms.")

    # 8
    R.h1("8. End-to-End Validation, Traceability and Contributions")
    R.h2("8.1 Completion and validity")
    R.p(f"The orchestrator ran all 10 stages successfully ({pipe.duration_seconds.sum():.0f} s) and passed six "
        "cross-unit checks; the test suite was re-run for this report: **222 passed, 0 failed**. **Threats:** the "
        "surge label derives from the input series (mitigated by lagging and a train-only cut-off check); AI "
        "reference labels; one country, category and season; search is a proxy for sales; Trends re-sampling.")
    R.table(["Objective", "Method", "Result", "Evidence", "Action"], [
        ["O1 Dataset", "Sec. 2", f"16,140/16,140 rows; {mpass} PASS", "Sec. 2.2; Appendix A", "R5"],
        ["O2 Surge alerts", "Sec. 4, RF", f"PR-AUC {rf.pr_auc:.3f} = {lift:.1f}× chance", f"Table {t_u2}, Fig. {f_u2}",
         "R2"],
        ["O3 Forecast", "Sec. 5.1, LightGBM", f"MAE −{mae_red:.1f}% vs naive", f"Table {t_m1}, Fig. {f_m1}", "R1"],
        ["O4 Opinion & lead", "Sec. 5.2", f"Acc. {vader.accuracy:.3f} > {alwayspos.accuracy:.3f}; no lead",
         f"Table {t_m2}, Figs. {f_top}, {f_ll}", "R3"],
        ["O5 Decision tool", "Sec. 7", "6 pages, filters; 222 tests pass", f"Figs. {f_d1}–{f_d2}", "R4"],
    ], "Objective-to-evidence traceability matrix", widths=[1.1, 1.1, 1.9, 1.6, 0.6], size=8.5, bold_first_col=True)
    R.h2("8.2 Project tracking and individual work")
    R.p("Work was tracked on a Jira Scrum board (key SCRUM; Backlog → To Do → In Progress → Review/Testing → "
        "Completed; link: **[insert Jira board URL]**) and merged via GitHub pull requests. Commit counts are "
        "from `git shortlog`; individual marks may vary with participation and explanation.")
    R.table(["Member / Reg. No.", "Work items (completed)", "Evidence"], [
        ["Mounik Sai (23CSE561)", "SCRUM-10, 14, 19, 24, 26, 32: documentation, pipeline consolidation, Method 2 "
         "isolation & audit, Method 1 benchmark, Viral Radar, presentations (4–21 Oct)",
         "forecast_method1.py, isolate_method2_data.py, radar.py; 38 commits"],
        ["Ashwin E (23CSE09)", "Task 1, SCRUM-23, 28, 33: collection, cleaning, alignment, EDA, text corpus and "
         "mining, Text Mining page (4–21 Oct)", "collect_youtube.py, text_mining.py; 12 commits"],
        ["Vijay Krishna (23CSE112)", "SCRUM-5, 12, 17, 21, 29: registry analysis, problem statement, cleaning "
         "docs, master dataset, text-mining evaluation (4–21 Oct)",
         "build_master.py, evaluate_text_mining.py; 29 commits"],
        ["Sisthick S (23CSE465)", "SCRUM-20, dashboard: set-up, encoding & scaling, Streamlit app, time-series "
         "evaluation (3–21 Oct)", "feature_encoding_scaling.py, dashboard/app.py; 30 commits"],
        ["Konduru Hemesh (23CSE724)", "SCRUM-22, 30: chronological split & stationarity, end-to-end orchestration "
         "and tests (20 Oct)", "prep_time_series.py, run_full_pipeline.py; 5 commits"],
    ], "Individual contributions and evidence", widths=[1.45, 3.15, 1.9], size=8.5, bold_first_col=True)
    R.h2("8.3 AI use and originality declarations")
    R.p("Anthropic Claude (via Claude Code and GitHub Copilot) assisted with code, documentation and drafting this "
        "report; the team verified every number against committed outputs, re-ran tests and corrected unsupported "
        "earlier slide claims (surge-classifier F1, sentiment lead time). An AI reviewer supplied 95 of 100 "
        "sentiment reference labels (flagged per row). Turnitin similarity: **[insert %]** (limit ≤ 15%).")

    # 9
    R.h1("9. Conclusion, Recommendations and Roadmap")
    R.p(f"All objectives were met (traceability matrix, Section 8.1): forecasts improve on naive by {mae_red:.1f}% "
        f"and surge alerts rank {lift:.1f}× better than chance; comment tone is not a leading indicator and "
        "forecasts lack directional skill, which shapes how the tool should be used.")
    R.table(["#", "Recommendation", "KPI effect", "Owner", "Feasibility"], [
        ["R1", "Daily forecasts for STABLE/DECLINING allocation", "MAE −8 to −11% vs naive", "Planners", "High"],
        ["R2", "RISING surge alerts as a daily triage list; threshold from cost ratio",
         "~40% of surges a day early", "Category managers", "Medium"],
        ["R3", "Sentiment/topics as brand-health monitor", "Early negative themes", "Marketing", "High"],
        ["R4", "One-season retail pilot of the dashboard", "Measured stock-out change", "Team + partner", "Medium"],
        ["R5", "Add sales and price data", "KPIs in units and ₹", "Partner data team", "Low–Medium"],
    ], "Prioritised recommendations", widths=[0.35, 2.7, 1.5, 1.2, 0.8], size=8.5)
    R.p("**Roadmap.** *0–1 month:* schedule daily collection and nightly pipeline runs (success: 30 failure-free "
        "days). *1–6 months:* partner pilot with cohort thresholds and launch-date features (success: alert "
        "precision ≥ 30%). *6–12 months:* sales data, Hinglish-aware sentiment, more categories. **Future work "
        "(not done):** interval and multi-day forecasts, Method 1 tuning, radar back-testing, larger human labelling, "
        "public hosting.")

    # References
    R.h1("References")
    refs = [
        "H. Choi and H. Varian. 2012. Predicting the Present with Google Trends. *Economic Record* 88, s1, 2–9.",
        "S. Asur and B. A. Huberman. 2010. Predicting the Future with Social Media. In *Proc. IEEE/WIC/ACM WI-IAT*, "
        "492–499.",
        "T. Boone, R. Ganeshan, A. Jain, and N. R. Sanders. 2019. Forecasting sales in the supply chain: Consumer "
        "analytics in the big data era. *International Journal of Forecasting* 35, 1, 170–180.",
        "R. Carbonneau, K. Laframboise, and R. Vahidov. 2008. Application of machine learning techniques for supply "
        "chain demand forecasting. *European Journal of Operational Research* 184, 3, 1140–1154.",
        "C. J. Hutto and E. Gilbert. 2014. VADER: A Parsimonious Rule-Based Model for Sentiment Analysis of Social "
        "Media Text. In *Proc. ICWSM*, 216–225.",
        "D. M. Blei, A. Y. Ng, and M. I. Jordan. 2003. Latent Dirichlet Allocation. *JMLR* 3, 993–1022.",
        "G. Ke et al. 2017. LightGBM: A Highly Efficient Gradient Boosting Decision Tree. In *NeurIPS 30*, 3146–3154.",
        "F. Pedregosa et al. 2011. Scikit-learn: Machine Learning in Python. *JMLR* 12, 2825–2830.",
        "S. Seabold and J. Perktold. 2010. Statsmodels: Econometric and Statistical Modeling with Python. In *Proc. "
        "9th Python in Science Conf.*, 92–96.",
        "L. Breiman. 2001. Random Forests. *Machine Learning* 45, 1, 5–32.",
        "D. D. Lee and H. S. Seung. 1999. Learning the parts of objects by non-negative matrix factorization. "
        "*Nature* 401, 788–791.",
        "Government of India. 2023. The Digital Personal Data Protection Act, 2023 (Act No. 22 of 2023).",
        "Streamlit Inc. 2026. Streamlit Documentation. https://docs.streamlit.io/",
        "R. J. Hyndman and A. B. Koehler. 2006. Another look at measures of forecast accuracy. *International "
        "Journal of Forecasting* 22, 4, 679–688.",
        "T. Saito and M. Rehmsmeier. 2015. The Precision-Recall Plot Is More Informative than the ROC Plot When "
        "Evaluating Binary Classifiers on Imbalanced Datasets. *PLOS ONE* 10, 3, e0118432.",
        "Google. 2026. YouTube Data API v3 and Google Trends. https://developers.google.com/youtube/v3; "
        "https://trends.google.com/ (accessed 3–4 Oct 2026).",
        f"Group 13. 2026. Viral Product Intelligence repository. {REPO}",
    ]
    for i, ref in enumerate(refs, 1):
        para = doc.add_paragraph()
        fmt = para.paragraph_format
        fmt.left_indent = Inches(0.4)
        fmt.first_line_indent = Inches(-0.4)
        fmt.space_after = Pt(2)
        fmt.line_spacing = 1.0
        set_run_font(para.add_run(f"[{i}]\t"), size=10)
        add_rich(para, ref, size=10)
    R.p("Scikit-learn [8], statsmodels [9] and Random Forest [10] underlie Sections 4–5; Google [16] is the data "
        "source; [17] holds all code and data.", size=10, spacing=1.0)

    # Appendices
    R.h1("Appendices", page_break=True)
    R.h2("Appendix A: Validation records")
    R.table(["Master dataset check", "Status", "Detail"],
            [[r.check, r.status, str(r.detail)[:95] + ("…" if len(str(r.detail)) > 95 else "")]
             for r in mval.itertuples()], "Master dataset validation (processed/master_validation_report.csv)",
            widths=[2.0, 0.55, 3.95], size=7.5)
    R.table(["Method 2 leakage check", "Status", "Detail"],
            [[r.check, r.status, str(r.detail)[:95]] for r in audit.itertuples()],
            "Method 2 leakage audit (processed/method2_leakage_audit_report.csv)", widths=[2.0, 0.55, 3.95], size=7.5)
    R.h2("Appendix B: Model tuning")
    R.table(["Model", "Settings", "CV PR-AUC (mean ± SD)", "Threshold", "CV F1"],
            [[r.model, r.params.replace("'", "")[:55], f"{r.cv_pr_auc_mean:.3f} ± {r.cv_pr_auc_std:.3f}",
              f"{r.oof_threshold:.2f}", f"{r.cv_f1_at_threshold:.3f}"] for r in u2cv.itertuples()],
            "Unit 2 expanding-window CV grid (5 date folds)", widths=[1.6, 2.5, 1.2, 0.6, 0.6], size=7.5)
    R.h2("Appendix C: Implementation and dashboard guide")
    R.table(["Stage", "Unit", "Status", "Seconds", "Details"],
            [[r.stage_name, r.unit, r.status, f"{r.duration_seconds:.1f}", str(r.details)[:70]]
             for r in pipe.itertuples()], "Pipeline execution record (processed/pipeline_execution_summary.csv)",
            widths=[1.3, 0.5, 0.7, 0.6, 3.4], size=7.5)
    R.p("**Guide:** run the app, choose filters in the sidebar (empty = all; they cascade and persist; *Reset "
        "filters* clears them), then switch pages. Config lives in `src/config.py`; seeds are 42; cut-off "
        "2026-08-12.", size=10, spacing=1.0)
    R.h2("Appendix D: Tracking, AI declaration and similarity report")
    R.p("Attach the Jira board screenshot (Completed column), the signed declaration (front matter, AI use in "
        "Section 8.3) and the Turnitin similarity report (**[insert %]**). Full commit history: `git log` in the "
        "repository.", size=10, spacing=1.0)

    doc.save(str(TARGET))
    print("saved", TARGET, R.counters)


if __name__ == "__main__":
    build()
