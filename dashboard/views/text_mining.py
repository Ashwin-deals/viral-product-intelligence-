"""Text mining page (SCRUM-33): the descriptive comment layer of SCRUM-28 in the dashboard.

Read-only view of the files src/text_mining.py and src/evaluate_spotcheck.py write to
data/processed/: VADER sentiment, LDA topics, aspect mentions, the product-level link to search
interest, a comment explorer and the spot-check result. Nothing is recomputed from the raw text;
the page only aggregates the per-comment scores for the products picked in the sidebar.

The comments are one sample collected on 2026-10-03/04, so the sidebar date range does not apply
here (the product filters do). A comment shared by several products counts once in any group.
"""
from dataclasses import replace
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import config
from data_loader import read_csv
from filters import COHORT_ORDER, apply_filters

FILES = {  # key -> file in data/processed/
    "comments": "text_sentiment_comments.csv",
    "corpus": "text_corpus.csv",
    "terms": "text_topics_terms.csv",
    "selection": "text_topic_model_selection.csv",
    "link": "text_signal_link.csv",
    "spot_eval": "text_sentiment_spotcheck_eval.csv",
    "full_report": "full_report.csv",
}
USECOLS = {  # the large files: only the columns this page needs
    "corpus": ["product_id", "comment_id", "text_raw"],
    "full_report": ["product_id", "mean_interest"],
}
LABELS = ("positive", "neutral", "negative")
NOT_SCORED = "not scored"
LABEL_COLORS = ("#2a78d6", "#c3c2b7", "#e34948")  # the SCRUM-28 chart colours, readable on light and dark
ASPECTS = {
    "camera": "Camera",
    "battery": "Battery",
    "performance": "Performance",
    "price_value": "Price / value",
    "design_display": "Design / display",
}
MAIN_VOCABULARY = "domain_stop_list_no_brand_terms"  # the reported fits in text_topic_model_selection.csv
TOP_N = 10
SMALL_SAMPLE = 30  # fewer usable comments than this: treat a product's mean with care
EXPLORER_CAP = 200
RERUN = "`python3 src/text_mining.py`"


# --- loading --------------------------------------------------------------------------------------
def read_text_csv(path, usecols=None):
    """data_loader.read_csv, plus: an empty file or one without the needed columns loads as empty."""
    try:
        if usecols is None:
            return read_csv(path, parse_dates=())
        return pd.read_csv(path, usecols=usecols) if Path(path).exists() else pd.DataFrame()
    except (pd.errors.EmptyDataError, ValueError):
        return pd.DataFrame()


def prepare_comments(df):
    """Types for the per-comment file: aspect flags as bools, topic as a nullable int, ids as text."""
    if df.empty or not {"product_id", "comment_id", "sentiment_compound"} <= set(df.columns):
        return pd.DataFrame()
    df = df.copy()
    df["comment_id"] = df["comment_id"].astype(str)
    df["sentiment_label"] = df["sentiment_label"].fillna("")
    for name in ASPECTS:
        col = f"aspect_{name}"
        df[col] = df[col].astype(str).eq("True") if col in df else False
    df["dominant_topic"] = pd.to_numeric(df.get("dominant_topic"), errors="coerce").astype("Int64")
    df["like_count"] = pd.to_numeric(df.get("like_count"), errors="coerce").fillna(0).astype(int)
    return df


def load_text_data(paths=None):
    """Every text-mining output as a dict of frames; a missing or empty file is an empty frame."""
    paths = paths or config.PATHS
    data = {key: read_text_csv(paths.processed_dir / name, USECOLS.get(key)) for key, name in FILES.items()}
    data["comments"] = prepare_comments(data["comments"])
    return data


@st.cache_data(show_spinner="Loading text-mining outputs…")
def get_text_data():
    return load_text_data()


# --- aggregation (pure pandas, unit-tested) ----------------------------------------------------------
def unique_comments(df):
    return df.drop_duplicates("comment_id")


def scored(df):
    return df[df["sentiment_compound"].notna()]


def with_catalog(df, catalog):
    return df.merge(catalog[["product_id", "product_name", "brand", "product_type"]], on="product_id", how="left")


def kpis(comments):
    unique = unique_comments(comments)
    usable = scored(unique)
    shares = usable["sentiment_label"].value_counts(normalize=True).reindex(LABELS, fill_value=0.0)
    return {"comments": len(unique), "usable": len(usable), "shares": shares,
            "mean": usable["sentiment_compound"].mean() if len(usable) else float("nan")}


def label_shares(comments, catalog, by):
    """Share of each sentiment label per product_type or brand (scored comments, a shared one once)."""
    rows = []
    for group, frame in with_catalog(scored(comments), catalog).groupby(by):
        frame = unique_comments(frame)
        counts = frame["sentiment_label"].value_counts().reindex(LABELS, fill_value=0)
        for order, label in enumerate(LABELS):
            rows.append({by: group, "label": label, "order": order, "comments": int(counts[label]),
                         "share": counts[label] / len(frame), "scored": len(frame)})
    return pd.DataFrame(rows, columns=[by, "label", "order", "comments", "share", "scored"])


def product_sentiment(comments, catalog):
    """Mean VADER compound and usable-comment count per product, with a label that shows n."""
    usable = scored(comments)
    out = usable.groupby("product_id", as_index=False).agg(
        mean_compound=("sentiment_compound", "mean"), usable=("sentiment_compound", "size"))
    out = with_catalog(out, catalog)
    out["product"] = out["product_id"] + " · " + out["product_name"].fillna("") + " (n=" + out["usable"].astype(str) + ")"
    return out.sort_values(["mean_compound", "product_id"], ascending=[False, True]).reset_index(drop=True)


def top_bottom(per_product, n=TOP_N):
    """(top, bottom) products by mean sentiment; bottom is empty when every product fits in top."""
    if len(per_product) <= 2 * n:
        return per_product, per_product.iloc[0:0]
    return per_product.head(n), per_product.tail(n)


def topic_columns(comments):
    cols = [c for c in comments.columns if c.startswith("topic_") and c.removeprefix("topic_").isdigit()]
    return sorted(cols, key=lambda c: int(c.removeprefix("topic_")))


def chosen_k(selection, method="lda"):
    """The k selected for a method in text_topic_model_selection.csv, or None."""
    if selection.empty or not {"method", "vocabulary", "k", "selected_for_method"} <= set(selection.columns):
        return None
    rows = selection[(selection["method"] == method) & (selection["vocabulary"] == MAIN_VOCABULARY)
                     & selection["selected_for_method"].astype(str).eq("True")]
    return int(rows["k"].iloc[0]) if len(rows) else None


def fitted_ks(selection):
    if selection.empty or not {"vocabulary", "k"} <= set(selection.columns):
        return []
    main = selection[selection["vocabulary"] == MAIN_VOCABULARY]
    return sorted(set(pd.to_numeric(main["k"], errors="coerce").dropna().astype(int)))


def topic_run_warning(selection, comments, k_range=config.TEXT_TOPIC_K_RANGE):
    """Text of a warning when the topic files do not come from the full run, else None."""
    ks = fitted_ks(selection)
    if ks and ks != sorted(k_range):
        return (f"These topics come from a quick test run that tried only k = {', '.join(map(str, ks))}, not the "
                f"full search over k = {min(k_range)} to {max(k_range)}; the smoke test of `src/run_full_pipeline.py` "
                f"overwrites the real output. Restore the full run with {RERUN} (about 4 minutes).")
    k, cols = chosen_k(selection), topic_columns(comments)
    if k is not None and cols and len(cols) != k:
        return (f"The topic files disagree: the model selection chose k = {k} but the comment file has "
                f"{len(cols)} topic columns. Rerun {RERUN}.")
    return None


def topic_labels(terms, method="lda"):
    """{topic number: "1 · label (suggested)"} from text_topics_terms.csv."""
    if terms.empty:
        return {}
    first = terms[terms["method"] == method].drop_duplicates("topic")
    return {int(r.topic): f"{int(r.topic)} · {r.label_suggestion}" for r in first.itertuples()}


def topic_terms_table(terms, method="lda"):
    """One row per topic: suggested label, mean share of comment text, dominant comments, top terms."""
    if terms.empty:
        return pd.DataFrame()
    rows = terms[terms["method"] == method].sort_values(["topic", "rank"])
    return rows.groupby("topic", as_index=False).agg(
        suggested_label=("label_suggestion", "first"), share=("topic_mean_share", "first"),
        dominant_comments=("dominant_comments", "first"), top_terms=("term", " ".join))


def topic_shares(comments, catalog, by, labels):
    """Mean topic share per group among comments that have a topic (long format; a shared comment once)."""
    cols = topic_columns(comments)
    if not cols:
        return pd.DataFrame(columns=[by, "topic", "share"])
    frames = []
    for group, frame in with_catalog(comments[comments["dominant_topic"].notna()], catalog).groupby(by):
        means = unique_comments(frame)[cols].mean()
        frames.append(pd.DataFrame({by: group, "topic": [labels.get(int(c.removeprefix("topic_")), c) for c in cols],
                                    "order": range(len(cols)), "share": means.values}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[by, "topic", "order", "share"])


def aspect_stats(comments):
    """Per aspect: share of usable comments mentioning it and their mean sentiment (a shared comment once)."""
    usable = scored(unique_comments(comments))
    rows = []
    for name, title in ASPECTS.items():
        mask = usable[f"aspect_{name}"]
        rows.append({"aspect": title, "comments": int(mask.sum()),
                     "share": mask.mean() if len(usable) else float("nan"),
                     "mean_compound": usable.loc[mask, "sentiment_compound"].mean() if mask.any() else float("nan")})
    return pd.DataFrame(rows)


def sentiment_vs_interest(per_product, full_report):
    if per_product.empty or full_report.empty:
        return pd.DataFrame()
    interest = full_report.assign(mean_interest=pd.to_numeric(full_report["mean_interest"], errors="coerce"))
    return per_product.merge(interest[["product_id", "mean_interest"]], on="product_id").dropna(
        subset=["mean_interest", "mean_compound"])


def link_row(link, text_measure="mean_compound", trends_measure="mean_interest"):
    if link.empty:
        return None
    rows = link[(link["text_measure"] == text_measure) & (link["trends_measure"] == trends_measure)]
    return rows.iloc[0] if len(rows) else None


def explore(comments, corpus, catalog, labels=LABELS, query="", cap=EXPLORER_CAP, topic_names=None):
    """(table, matching) for the comment explorer: one row per comment, most-liked first, at most cap rows.

    labels may include NOT_SCORED for flagged rows; query is a case-insensitive substring of the text.
    A comment shared by several products shows once, with every product it belongs to.
    """
    if comments.empty or corpus.empty:
        return pd.DataFrame(), 0
    df = comments.assign(sentiment=comments["sentiment_label"].replace("", NOT_SCORED))
    df = df[df["sentiment"].isin(labels)]
    text = corpus.assign(comment_id=corpus["comment_id"].astype(str)).drop_duplicates(["product_id", "comment_id"])
    df = df.merge(text, on=["product_id", "comment_id"], how="inner")
    if query.strip():
        df = df[df["text_raw"].fillna("").str.contains(query.strip(), case=False, regex=False)]
    if df.empty:
        return pd.DataFrame(), 0
    df = with_catalog(df, catalog)
    products = df.groupby("comment_id")["product_name"].agg(lambda s: ", ".join(sorted(s.dropna().unique())))
    df = unique_comments(df.sort_values(["like_count", "comment_id"], ascending=[False, True]))
    topic_names = topic_names or {}
    table = pd.DataFrame({
        "comment": df["text_raw"].values,
        "products": df["comment_id"].map(products).values,
        "sentiment": df["sentiment"].values,
        "score": df["sentiment_compound"].values,
        "topic": [topic_names.get(int(t), str(int(t))) if pd.notna(t) else "" for t in df["dominant_topic"]],
        "likes": df["like_count"].values,
    })
    return table.head(cap).reset_index(drop=True), len(table)


def spotcheck_summary(spot_eval):
    """Numbers of the spot check from text_sentiment_spotcheck_eval.csv, or None if it is missing."""
    if spot_eval.empty or not {"section", "metric", "value"} <= set(spot_eval.columns):
        return None
    summary = spot_eval[spot_eval["section"] == "summary"].set_index("metric")["value"]
    needed = ("compared", "agreement", "agreement_ci95_low", "agreement_ci95_high")
    if not set(needed) <= set(summary.index):
        return None
    per_class = spot_eval[spot_eval["section"] == "per_class"]
    recall = per_class[per_class["metric"] == "recall"].set_index("ai_label")["value"]
    num = lambda v: pd.to_numeric(v, errors="coerce")  # noqa: E731
    return {
        "compared": int(num(summary["compared"])),
        "excluded": int(num(summary.get("excluded_unclear", 0))),
        "agreement": num(summary["agreement"]),
        "low": num(summary["agreement_ci95_low"]),
        "high": num(summary["agreement_ci95_high"]),
        "disagreements": int(num(summary.get("disagreements", 0))),
        "no_words": int(num(summary.get("disagreements_vader_neutral_no_sentiment_words", 0))),
        "recall": {label: num(recall.get(label)) for label in LABELS},
        "source": str(spot_eval["label_source"].dropna().iloc[0]) if "label_source" in spot_eval
        and spot_eval["label_source"].notna().any() else "AI review, not human",
    }


# --- rendering -----------------------------------------------------------------------------------------
def _height(rows, step=28, extra=120):
    """Chart height in pixels: Streamlit fits title, axes and legend inside it, so add room for them."""
    return extra + step * max(rows, 1)


def _pct(x):
    return "–" if pd.isna(x) else f"{x:.0%}"


def _missing(name):
    st.info(f"`data/processed/{name}` is missing or empty. Run {RERUN} to create it.")


def _label_color():
    return alt.Color("label:N", title="VADER label", sort=list(LABELS),
                     scale=alt.Scale(domain=list(LABELS), range=list(LABEL_COLORS)))


def _cohort_color():
    return alt.Color("product_type:N", title="Cohort", sort=COHORT_ORDER)


def _stacked_labels(shares, by, title):
    return alt.Chart(shares).mark_bar().encode(
        x=alt.X("share:Q", title="Share of scored comments", axis=alt.Axis(format="%"), stack="normalize"),
        y=alt.Y(f"{by}:N", title=None, sort=COHORT_ORDER if by == "product_type" else None),
        color=_label_color(), order=alt.Order("order:Q"),
        tooltip=[f"{by}:N", "label:N", alt.Tooltip("share:Q", format=".1%"), "comments:Q",
                 alt.Tooltip("scored:Q", title="Scored comments")],
    ).properties(title=title, height=_height(shares[by].nunique()))


def _product_bars(df, title):
    return alt.Chart(df).mark_bar().encode(
        x=alt.X("mean_compound:Q", title="Mean VADER compound (-1 to 1)", scale=alt.Scale(zero=True)),
        y=alt.Y("product:N", title=None, sort="-x", axis=alt.Axis(labelLimit=320)),
        color=_cohort_color(),
        tooltip=["product:N", "brand:N", "product_type:N", alt.Tooltip("mean_compound:Q", format=".3f"),
                 alt.Tooltip("usable:Q", title="Usable comments")],
    ).properties(title=title, height=_height(len(df)))


def render_kpis(comments, selection):
    k = kpis(comments)
    lda_k = chosen_k(selection)
    test_run = bool(fitted_ks(selection)) and fitted_ks(selection) != sorted(config.TEXT_TOPIC_K_RANGE)
    cols = st.columns(7)
    cols[0].metric("Comments", f"{k['comments']:,}", help="Comments analysed: unique comments of the selected products.")
    cols[1].metric("Usable", f"{k['usable']:,}",
                   help="Usable comments, scored by VADER; emoji-only, non-English and too-short comments are left unscored.")
    for col, label in zip(cols[2:5], LABELS):
        col.metric(label.capitalize(), _pct(k["shares"][label]), help=f"Share of usable comments VADER labels {label}.")
    cols[5].metric("Mean sentiment", "–" if pd.isna(k["mean"]) else f"{k['mean']:.2f}",
                   help="Mean VADER compound score of the usable comments, from -1 (negative) to 1 (positive).")
    cols[6].metric("Topics (LDA k)", "–" if lda_k is None else str(lda_k),
                   delta="quick test run" if test_run else None, delta_color="off", delta_arrow="off",
                   help="Number of LDA topics, i.e. the k chosen by coherence and stability.")


def render_sentiment(comments, catalog):
    st.subheader("Sentiment overview")
    st.caption("What this shows: how the comments split into positive, neutral and negative by cohort and brand, "
               "and which products' comments are the most and least positive on average.")
    left, right = st.columns(2)
    left.altair_chart(_stacked_labels(label_shares(comments, catalog, "product_type"), "product_type",
                                      "Label shares by cohort"), width="stretch")
    right.altair_chart(_stacked_labels(label_shares(comments, catalog, "brand"), "brand",
                                       "Label shares by brand"), width="stretch")

    top, bottom = top_bottom(product_sentiment(comments, catalog))
    if bottom.empty:
        st.altair_chart(_product_bars(top, "Products by mean sentiment"), width="stretch")
    else:
        st.altair_chart(_product_bars(top, f"Top {TOP_N} products by mean sentiment"), width="stretch")
        st.altair_chart(_product_bars(bottom, f"Bottom {TOP_N} products by mean sentiment"), width="stretch")
    st.caption(f"n = usable comments per product; a mean from fewer than {SMALL_SAMPLE} comments is unreliable.")


def render_topics(comments, catalog, text):
    st.subheader("Topics")
    st.caption("What this shows: the themes LDA found in the comment words, how much of the comment text each "
               "takes up per cohort, and the mix for each selected product.")
    terms, selection = text["terms"], text["selection"]
    warning = topic_run_warning(selection, comments)
    if warning:
        st.warning(warning, icon=":material/warning:")
    if terms.empty or not topic_columns(comments):
        _missing(FILES["terms"])
        return

    labels = topic_labels(terms)
    table = topic_terms_table(terms)
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "topic": "Topic",
        "suggested_label": st.column_config.TextColumn(
            "Suggested label", help="Suggested from the top terms; confirm it by reading them."),
        "share": st.column_config.ProgressColumn("Share of comment text", min_value=0.0, max_value=1.0,
                                                 format="percent"),
        "dominant_comments": st.column_config.NumberColumn("Comments where dominant", format="%d"),
        "top_terms": "Top 10 terms",
    })
    nmf = topic_terms_table(terms, "nmf")
    if not nmf.empty:
        with st.expander("NMF topics (comparison model)"):
            st.dataframe(nmf[["topic", "top_terms"]], hide_index=True, width="stretch")
            st.caption("A theme that both LDA and NMF find is more trustworthy than any single topic.")

    topic_color = alt.Color("topic:N", title="Topic (suggested label)", sort=list(labels.values()))
    by_type = topic_shares(comments, catalog, "product_type", labels)
    chart = alt.Chart(by_type).mark_bar().encode(
        x=alt.X("share:Q", title="Mean topic share", axis=alt.Axis(format="%"), stack="normalize"),
        y=alt.Y("product_type:N", title=None, sort=COHORT_ORDER),
        color=topic_color, order=alt.Order("order:Q"),
        tooltip=["product_type:N", "topic:N", alt.Tooltip("share:Q", format=".1%")],
    ).properties(title="Topic share by cohort", height=_height(by_type["product_type"].nunique()))
    st.altair_chart(chart, width="stretch")

    by_product = topic_shares(comments, catalog, "product_id", labels)
    by_product["product"] = by_product["product_id"].map(dict(zip(catalog["product_id"],
                                                                  catalog["product_id"] + " · " + catalog["product_name"])))
    chart = alt.Chart(by_product).mark_bar().encode(
        x=alt.X("share:Q", title="Mean topic share", axis=alt.Axis(format="%"), stack="normalize"),
        y=alt.Y("product:N", title=None, axis=alt.Axis(labelLimit=320)),
        color=topic_color, order=alt.Order("order:Q"),
        tooltip=["product:N", "topic:N", alt.Tooltip("share:Q", format=".1%")],
    ).properties(title="Topic share for the selected products", height=_height(by_product["product"].nunique(), step=22))
    st.altair_chart(chart, width="stretch")


def render_aspects(comments):
    st.subheader("Aspects")
    st.caption("What this shows: how often usable comments mention each phone aspect (SCRUM-24 keywords) and "
               "how positive those comments are on average.")
    stats = aspect_stats(comments)
    tooltip = ["aspect:N", alt.Tooltip("comments:Q", title="Comments mentioning"),
               alt.Tooltip("share:Q", format=".1%"), alt.Tooltip("mean_compound:Q", format=".3f")]
    left, right = st.columns(2)
    left.altair_chart(alt.Chart(stats).mark_bar().encode(
        x=alt.X("share:Q", title="Share of usable comments mentioning", axis=alt.Axis(format="%")),
        y=alt.Y("aspect:N", title=None, sort=list(ASPECTS.values())), tooltip=tooltip,
    ).properties(title="Mentions per aspect", height=_height(len(ASPECTS))), width="stretch")
    right.altair_chart(alt.Chart(stats).mark_bar().encode(
        x=alt.X("mean_compound:Q", title="Mean VADER compound of those comments", scale=alt.Scale(zero=True)),
        y=alt.Y("aspect:N", title=None, sort=list(ASPECTS.values())), tooltip=tooltip,
    ).properties(title="Sentiment per aspect", height=_height(len(ASPECTS))), width="stretch")
    st.caption("A comment can mention several aspects. Keyword matching misses synonyms and other languages.")


def render_link(comments, catalog, text):
    st.subheader("Sentiment vs search interest")
    st.caption("What this shows: each product's mean comment sentiment against its mean Google Trends interest.")
    merged = sentiment_vs_interest(product_sentiment(comments, catalog), text["full_report"])
    if merged.empty:
        _missing(FILES["full_report"])
        return
    chart = alt.Chart(merged).mark_circle(size=90, opacity=0.85).encode(
        x=alt.X("mean_interest:Q", title="Mean search interest (0-100)"),
        y=alt.Y("mean_compound:Q", title="Mean VADER compound"),
        color=_cohort_color(),
        tooltip=["product:N", "brand:N", "product_type:N", alt.Tooltip("mean_interest:Q", format=".1f"),
                 alt.Tooltip("mean_compound:Q", format=".3f"), alt.Tooltip("usable:Q", title="Usable comments")],
    ).properties(title="Per-product sentiment vs search interest", height=340)
    st.altair_chart(chart, width="stretch")
    row = link_row(text["link"])
    if row is None or pd.isna(row["spearman_rho"]):
        st.caption(f"No correlation available: `data/processed/{FILES['link']}` is missing. The link is "
                   "descriptive, not causal.")
        return
    st.caption(f"Spearman ρ = {row['spearman_rho']:.2f} (p = {row['spearman_p']:.3f}, n = {int(row['n_products'])} "
               f"products, all registry products, not just the selection): a {row['strength_spearman']} link at "
               "best. It describes one snapshot and is not causal: positive comments do not make search interest "
               "rise, and both may follow how well known a phone is.")


def render_explorer(comments, catalog, text):
    st.subheader("Comment explorer")
    st.caption("What this shows: the comments behind the numbers, most-liked first, with VADER's label and the "
               "dominant topic. Text only; author names are never stored.")
    if text["corpus"].empty:
        _missing(FILES["corpus"])
        return
    left, right = st.columns([1, 2])
    labels = left.multiselect("Sentiment label", [*LABELS, NOT_SCORED], default=list(LABELS), key="tm_labels")
    query = right.text_input("Search comment text", key="tm_query", placeholder="e.g. battery, heating, camera")
    table, matching = explore(comments, text["corpus"], catalog, labels, query,
                              topic_names=topic_labels(text["terms"]))
    if table.empty:
        st.info("No comments match these filters.")
        return
    st.caption(f"Showing {len(table):,} of {matching:,} matching comments (capped at {EXPLORER_CAP}).")
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "comment": st.column_config.TextColumn("Comment", width="large"),
        "products": "Product(s)",
        "sentiment": "VADER label",
        "score": st.column_config.NumberColumn("Compound", format="%.3f"),
        "topic": "Dominant topic (suggested label)",
        "likes": st.column_config.NumberColumn("Likes", format="%d"),
    })


def render_trust(text):
    st.subheader("How far to trust this")
    with st.container(border=True):
        spot = spotcheck_summary(text["spot_eval"])
        if spot is None:
            st.markdown(f"The spot-check result (`data/processed/{FILES['spot_eval']}`) is missing; run "
                        "`python3 src/evaluate_spotcheck.py`.")
        else:
            st.markdown(
                f"**Spot check, against an AI review, not human labels** (label source: *{spot['source']}*). "
                f"On {spot['compared']} sampled comments ({spot['excluded']} the review marked unclear were excluded), VADER "
                f"agrees with the AI review on **{_pct(spot['agreement'])}** (95% interval {_pct(spot['low'])} to "
                f"{_pct(spot['high'])}). It finds only {_pct(spot['recall']['negative'])} of the comments the "
                f"review read as negative: complaints without strong words, sarcasm and Hinglish are its weak "
                f"spots. In {spot['no_words']} of {spot['disagreements']} disagreements VADER found no sentiment "
                "word at all. This is agreement with another automatic reader, not accuracy.")
        st.markdown(
            "**Other limits**\n"
            "- One sample: comments collected on 2026-10-03/04, not a time series; the "
            "sidebar date range does not apply here.\n"
            "- Descriptive only: these scores are not model inputs. Method 2 uses the leak-free lagged features "
            "in `processed/method2_*` (SCRUM-24).\n"
            "- Short comments (median 5 tokens after cleaning) and generic words make topics weak; the suggested labels are "
            "guesses to confirm by reading the terms.\n"
            "- A comment can belong to several products whose searches overlap; it counts once in any group.")


def render(ctx, text=None):
    st.title("Text mining")
    st.caption(ctx.summary)
    text = get_text_data() if text is None else text
    if text["comments"].empty:
        _missing(FILES["comments"])
        return
    comments = apply_filters(text["comments"], replace(ctx.state, date_range=None), ctx.data.catalog)
    if comments.empty:
        st.warning("No comments for this selection. Adjust the sidebar filters.")
        return

    st.caption("What this shows: how many comments the selection has and their overall VADER sentiment. "
               "The sidebar date range does not apply: the comments are one sample from 2026-10-03/04.")
    render_kpis(comments, text["selection"])
    render_sentiment(comments, ctx.data.catalog)
    render_topics(comments, ctx.data.catalog, text)
    render_aspects(comments)
    render_link(comments, ctx.data.catalog, text)
    render_explorer(comments, ctx.data.catalog, text)
    render_trust(text)


if __name__ == "__main__":  # executed by st.navigation as a page script
    from context import current

    render(current())