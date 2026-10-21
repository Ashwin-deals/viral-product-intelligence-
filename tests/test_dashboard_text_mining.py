"""Text Mining dashboard page (SCRUM-33): aggregation logic on tmp-folder fixtures, plus headless renders."""
import pandas as pd
import pytest

pytest.importorskip("streamlit")
pytest.importorskip("altair")
import filters as flt  # noqa: E402
from views import text_mining as tm  # noqa: E402

CATALOG = pd.DataFrame([
    {"product_id": "P001", "product_name": "Apple iPhone 18 Pro", "brand": "Apple", "category": "Smartphones",
     "product_type": "RISING", "launch_period": "2026-09"},
    {"product_id": "P002", "product_name": "Google Pixel 11", "brand": "Google", "category": "Smartphones",
     "product_type": "RISING", "launch_period": "2026-08"},
])
COMMENTS = [  # product, comment, likes, compound, label, dominant topic, camera mention, text
    ("P001", "c1", 50, 0.6, "positive", 1, True, "Camera is AMAZING"),
    ("P001", "c2", 5, -0.4, "negative", 2, False, "battery drain is bad"),
    ("P001", "c3", 0, None, "", None, False, "🔥🔥"),  # flagged: not scored
    ("P001", "c4", 9, 0.0, "neutral", 1, True, "which camera sensor"),
    ("P002", "c4", 9, 0.0, "neutral", 1, True, "which camera sensor"),  # shared by both products
    ("P002", "c5", 1, 0.5, "positive", 2, False, "great price"),
]


def comments_frame(k=3):
    rows = []
    for pid, cid, likes, compound, label, topic, camera, _ in COMMENTS:
        row = {"product_id": pid, "comment_id": cid, "like_count": likes, "sentiment_compound": compound,
               "sentiment_label": label, "sentiment_skip_reason": "" if label else "emoji_only",
               "dominant_topic": topic}
        for name in tm.ASPECTS:
            row[f"aspect_{name}"] = "" if not label else str(camera if name == "camera" else False)
        for t in range(1, k + 1):
            row[f"topic_{t}"] = None if topic is None else (0.8 if t == topic else 0.2 / (k - 1))
        rows.append(row)
    return pd.DataFrame(rows)


def selection_frame(ks, chosen):
    rows = [{"method": m, "vocabulary": tm.MAIN_VOCABULARY, "k": k, "selected_for_method": k == chosen}
            for m in ("lda", "nmf") for k in ks]
    return pd.DataFrame(rows + [{"method": "lda", "vocabulary": "brand_and_model_terms_kept", "k": chosen,
                                 "selected_for_method": False}])


def terms_frame(k=3):
    labels = ["Buying advice", "Upgrading & ownership", "Design & display", "Camera"]
    return pd.DataFrame([{"method": m, "k": k, "topic": t, "label_suggestion": labels[t - 1], "rank": r,
                          "term": f"{m}{t}w{r}", "weight": 0.1, "topic_mean_share": 1 / k, "dominant_comments": 2}
                         for m in ("lda", "nmf") for t in range(1, k + 1) for r in (1, 2)])


def spot_eval_frame():
    summary = {"compared": 87, "excluded_unclear": 13, "agreement": 0.5517, "agreement_ci95_low": 0.4472,
               "agreement_ci95_high": 0.6518, "disagreements": 39,
               "disagreements_vader_neutral_no_sentiment_words": 12}
    rows = [{"section": "summary", "metric": m, "ai_label": "", "value": v} for m, v in summary.items()]
    rows += [{"section": "per_class", "metric": "recall", "ai_label": lab, "value": v}
             for lab, v in (("positive", 0.71), ("neutral", 0.54), ("negative", 0.32))]
    return pd.DataFrame(rows).assign(label_source="AI review, not human")


def write_outputs(paths, k=3, ks=(3,)):
    d = paths.processed_dir
    d.mkdir(parents=True, exist_ok=True)
    comments_frame(k).to_csv(d / tm.FILES["comments"], index=False)
    pd.DataFrame([{"product_id": p, "comment_id": c, "text_raw": text, "tokens": "x"}
                  for p, c, *_, text in COMMENTS]).to_csv(d / tm.FILES["corpus"], index=False)
    terms_frame(k).to_csv(d / tm.FILES["terms"], index=False)
    selection_frame(ks, k).to_csv(d / tm.FILES["selection"], index=False)
    pd.DataFrame([{"text_measure": "mean_compound", "trends_measure": "mean_interest", "n_products": 2,
                   "spearman_rho": 0.28, "spearman_p": 0.03, "strength_spearman": "weak"}]).to_csv(
        d / tm.FILES["link"], index=False)
    spot_eval_frame().to_csv(d / tm.FILES["spot_eval"], index=False)
    pd.DataFrame({"product_id": ["P001", "P002"], "mean_interest": [6.1, 7.0], "zero_share": [0, 0]}).to_csv(
        d / tm.FILES["full_report"], index=False)


@pytest.fixture
def text(paths):
    write_outputs(paths)
    return tm.load_text_data(paths)


# --- loading ---------------------------------------------------------------------------------------
def test_missing_and_empty_files_load_as_empty_frames(paths):
    data = tm.load_text_data(paths)
    assert set(data) == set(tm.FILES) and all(df.empty for df in data.values())
    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    for name in tm.FILES.values():
        (paths.processed_dir / name).write_text("", encoding="utf-8")
    assert all(df.empty for df in tm.load_text_data(paths).values())


def test_loader_types_and_keeps_only_needed_corpus_columns(text):
    comments = text["comments"]
    assert comments["aspect_camera"].dtype == bool and not comments.loc[2, "aspect_camera"]  # unscored: False
    assert str(comments["dominant_topic"].dtype) == "Int64" and comments["sentiment_label"][2] == ""
    assert list(text["corpus"].columns) == ["product_id", "comment_id", "text_raw"]


# --- aggregation -------------------------------------------------------------------------------------
def test_kpis_count_a_shared_comment_once(text):
    k = tm.kpis(text["comments"])
    assert k["comments"] == 5 and k["usable"] == 4  # c4 once; c3 unscored
    assert dict(k["shares"]) == {"positive": 0.5, "neutral": 0.25, "negative": 0.25}
    assert k["mean"] == pytest.approx((0.6 - 0.4 + 0.0 + 0.5) / 4)


def test_label_shares_by_group_dedupe_within_group(text):
    by_type = tm.label_shares(text["comments"], CATALOG, "product_type")
    rising = by_type.set_index("label")
    assert rising["scored"].unique().tolist() == [4] and rising.loc["neutral", "comments"] == 1
    by_brand = tm.label_shares(text["comments"], CATALOG, "brand").set_index(["brand", "label"])
    assert by_brand.loc[("Google", "neutral"), "share"] == 0.5 and by_brand.loc[("Apple", "positive"), "scored"] == 3


def test_product_sentiment_shows_sample_size_and_top_bottom_split(text):
    per = tm.product_sentiment(text["comments"], CATALOG)
    assert list(per["product_id"]) == ["P002", "P001"]
    assert per.loc[0, "product"] == "P002 · Google Pixel 11 (n=2)" and per.loc[1, "usable"] == 3
    top, bottom = tm.top_bottom(per, n=1)  # 2 products fit in one chart: no bottom chart
    assert len(top) == 2 and bottom.empty
    three = pd.DataFrame({"product_id": ["A", "B", "C"], "mean_compound": [0.5, 0.2, -0.1]})
    top, bottom = tm.top_bottom(three, n=1)
    assert list(top["product_id"]) == ["A"] and list(bottom["product_id"]) == ["C"]


def test_topic_and_aspect_shares(text):
    labels = tm.topic_labels(text["terms"])
    assert labels == {1: "1 · Buying advice", 2: "2 · Upgrading & ownership", 3: "3 · Design & display"}
    shares = tm.topic_shares(text["comments"], CATALOG, "product_type", labels).set_index("topic")["share"]
    assert shares.sum() == pytest.approx(1) and shares["1 · Buying advice"] == pytest.approx((0.8 + 0.8 + 0.1 + 0.1) / 4)
    table = tm.topic_terms_table(text["terms"])
    assert list(table["top_terms"]) == ["lda1w1 lda1w2", "lda2w1 lda2w2", "lda3w1 lda3w2"]
    aspects = tm.aspect_stats(text["comments"]).set_index("aspect")
    assert aspects.loc["Camera", "comments"] == 2 and aspects.loc["Camera", "share"] == 0.5
    assert aspects.loc["Camera", "mean_compound"] == pytest.approx(0.3)
    assert aspects.loc["Battery", "comments"] == 0 and pd.isna(aspects.loc["Battery", "mean_compound"])


@pytest.mark.parametrize("ks, k, columns, expect", [
    ((3,), 3, 3, "quick test run"),  # a smoke test overwrote the real output
    (tuple(range(4, 11)), 4, 4, None),  # the full run
    (tuple(range(4, 11)), 4, 3, "disagree"),  # files out of sync
])
def test_topic_run_warning(ks, k, columns, expect):
    warning = tm.topic_run_warning(selection_frame(ks, k), comments_frame(columns))
    if expect is None:
        assert warning is None
    else:
        assert expect in warning and "python3 src/text_mining.py" in warning
    assert tm.topic_run_warning(pd.DataFrame(), comments_frame(3)) is None
    assert tm.chosen_k(selection_frame(ks, k)) == k and tm.chosen_k(pd.DataFrame()) is None


def test_explorer_filters_searches_dedupes_and_caps(text):
    comments, corpus = text["comments"], text["corpus"]
    table, matching = tm.explore(comments, corpus, CATALOG)
    assert matching == 4 and list(table["likes"]) == [50, 9, 5, 1]  # scored only, most-liked first
    assert table.loc[1, "products"] == "Apple iPhone 18 Pro, Google Pixel 11"  # shared comment: one row
    assert list(table.columns) == ["comment", "products", "sentiment", "score", "topic", "likes"]
    table, matching = tm.explore(comments, corpus, CATALOG, labels=[tm.NOT_SCORED])
    assert matching == 1 and table.loc[0, "comment"] == "🔥🔥" and table.loc[0, "topic"] == ""
    table, _ = tm.explore(comments, corpus, CATALOG, query="  camera ", topic_names=tm.topic_labels(text["terms"]))
    assert list(table["comment"]) == ["Camera is AMAZING", "which camera sensor"]
    assert table.loc[0, "topic"] == "1 · Buying advice"
    table, matching = tm.explore(comments, corpus, CATALOG, query="no such words")
    assert table.empty and matching == 0
    assert tm.explore(comments, pd.DataFrame(), CATALOG)[0].empty

    many = pd.concat([comments.assign(comment_id=comments["comment_id"] + f"_{i}") for i in range(80)])
    many_text = pd.concat([corpus.assign(comment_id=corpus["comment_id"] + f"_{i}") for i in range(80)])
    table, matching = tm.explore(many, many_text, CATALOG)
    assert len(table) == tm.EXPLORER_CAP == 200 and matching == 320


def test_explorer_never_shows_author_names(text):
    corpus = text["corpus"].assign(author="Someone")  # even if a corpus ever had one
    table, _ = tm.explore(text["comments"], corpus, CATALOG)
    assert "author" not in " ".join(table.columns).lower()


def test_spotcheck_summary_reads_numbers_from_the_eval_file(text):
    spot = tm.spotcheck_summary(text["spot_eval"])
    assert spot["compared"] == 87 and spot["agreement"] == 0.5517 and spot["low"] == 0.4472
    assert spot["recall"]["negative"] == 0.32 and spot["source"] == "AI review, not human"
    assert tm.spotcheck_summary(pd.DataFrame()) is None


def test_sentiment_vs_interest_and_link_row(text):
    merged = tm.sentiment_vs_interest(tm.product_sentiment(text["comments"], CATALOG), text["full_report"])
    assert set(merged["product_id"]) == {"P001", "P002"}
    assert tm.link_row(text["link"])["spearman_rho"] == 0.28 and tm.link_row(pd.DataFrame()) is None


# --- headless renders -----------------------------------------------------------------------------------
def _render_page(root, products):
    """AppTest script: the page with a tmp-folder data root and a fixed product selection."""
    import pandas as pd

    import config
    import context
    import filters as flt
    from data_loader import Datasets
    from views import text_mining as tm

    catalog = pd.DataFrame([
        {"product_id": "P001", "product_name": "Apple iPhone 18 Pro", "brand": "Apple", "category": "Smartphones",
         "product_type": "RISING", "launch_period": "2026-09"},
        {"product_id": "P002", "product_name": "Google Pixel 11", "brand": "Google", "category": "Smartphones",
         "product_type": "RISING", "launch_period": "2026-08"},
    ])
    empty = pd.DataFrame()
    data = Datasets(catalog=catalog, master=empty, forecasts=empty, signals=empty, model_comparison=empty,
                    feature_importance=empty)
    ctx = context.PageContext(data=data, state=flt.FilterState(products=tuple(products)))
    tm.render(ctx, text=tm.load_text_data(config.Paths.from_root(root)))


def _run(root, products=()):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_function(_render_page, args=(str(root), products), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_page_renders_on_fixture_data_with_k3_warning(paths):
    write_outputs(paths)
    at = _run(paths.root)
    assert [s.value for s in at.subheader] == ["Sentiment overview", "Topics", "Aspects",
                                               "Sentiment vs search interest", "Comment explorer",
                                               "How far to trust this"]
    assert any("quick test run" in w.value for w in at.warning)
    topics = next(m for m in at.metric if m.label == "Topics (LDA k)")
    assert topics.value == "3" and topics.delta == "quick test run"
    trust = " ".join(m.value for m in at.markdown)
    assert "not human labels" in trust and "55%" in trust and "32%" in trust
    assert "human accuracy" not in trust.lower()


def test_page_without_k3_problem_shows_no_warning(paths):
    write_outputs(paths, k=4, ks=tuple(range(4, 11)))
    at = _run(paths.root)
    topics = next(m for m in at.metric if m.label == "Topics (LDA k)")
    assert not at.warning and topics.value == "4" and not topics.delta


def test_page_handles_missing_data_and_empty_selection(paths):
    at = _run(paths.root)
    assert "text_sentiment_comments.csv" in at.info[0].value and not at.subheader
    write_outputs(paths)
    at = _run(paths.root, products=("P999",))  # nothing in the catalog matches
    assert "No comments for this selection" in at.warning[0].value


def test_page_renders_on_real_data():
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(flt.__file__).replace("filters.py", "app.py"), default_timeout=90)
    at.run()
    at.switch_page("views/text_mining.py").run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Text mining"
