import csv
import math

import pandas as pd
import pytest

import build_text_corpus as tc
from conftest import PRODUCTS, read_csv

pytest.importorskip("vaderSentiment")  # SCRUM-28 needs VADER (requirements.txt)
import text_mining as tm  # noqa: E402

PROTECTED = tc.protected_terms(PRODUCTS)
TEXTS = [  # (product, text, likes); varied enough for small topic models
    ("P001", "the camera zoom is amazing in night photos", "12"),
    ("P001", "portrait photos and selfie camera look sharp and clean", "3"),
    ("P001", "zoom lens sensor beats every rival camera this year", "0"),
    ("P001", "battery backup is great and charging is quick", "5"),
    ("P001", "charger missing from the box but battery lasts two days", "1"),
    ("P001", "price is too expensive for this budget segment", "8"),
    ("P001", "this camera is not good and the battery is not great", "40"),
    ("P001", "display looks premium with thin bezel design", "2"),
    ("P001", "the iphone 18 pro specs sheet in this video", "0"),  # no VADER word: compound 0
    ("P002", "worth the money at this sale price honestly", "4"),
    ("P002", "battery drain and heating while gaming is terrible", "9"),
    ("P002", "snapdragon processor gaming performance smooth fps", "0"),
    ("P002", "camera photos at night are noisy and disappointing", "6"),
    ("P002", "screen colors amoled display design premium build", "1"),
    ("P002", "discount offer makes the price a great deal", "0"),
    ("P002", "charging speed and battery life are excellent", "2"),
    ("P002", "pixel 11 camera zoom photos look natural", "7"),
    ("P002", "nice video", "0"),  # too short after cleaning
]
FLAGGED = [  # (product, text, flags)
    ("P001", "🔥🔥", {"is_emoji_only": True, "is_empty_after_clean": True, "is_too_short": True}),
    ("P002", "बहुत अच्छा फ़ोन है भाई", {"likely_non_english": True}),
]
HEADER = ["product_id", "comment_id", "video_id", "query_version", "first_snapshot_date", "n_snapshots",
          "n_products_sharing", "published_at", "like_count", "text_raw", "text_sentiment_ready", "text_clean",
          "tokens", "n_tokens", "is_emoji_only", "likely_non_english", "is_empty_after_clean", "is_too_short",
          "is_usable"]


def corpus_row(pid, cid, text, likes="0", sharing="1", **flags):
    ready, _ = tc.sentiment_ready(text)
    tokens = tc.topic_tokens(ready, PROTECTED)
    f = {"is_emoji_only": False, "likely_non_english": False, "is_empty_after_clean": not tokens,
         "is_too_short": len(tokens) < 3}
    f.update(flags)
    return [pid, cid, "v" + pid, "v1", "2026-10-04", "1", sharing, "2026-10-01T10:00:00Z", likes, text, ready,
            " ".join(tokens), " ".join(tokens), str(len(tokens)), *(str(f[k]) for k in HEADER[14:18]),
            str(not any(f.values()))]


def setup(paths):
    rows = [corpus_row(pid, f"c{i}", text, likes) for i, (pid, text, likes) in enumerate(TEXTS)]
    rows += [corpus_row(pid, f"f{i}", text, **flags) for i, (pid, text, flags) in enumerate(FLAGGED)]
    rows.append(corpus_row("P002", "c0", TEXTS[0][1], "12", sharing="2"))  # c0 is shared by both products
    rows[0][6] = "2"
    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    with open(paths.processed_dir / tc.CORPUS_OUT, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([HEADER] + rows)
    pd.DataFrame({"product_id": ["P001", "P002"], "mean_interest": ["30.5", "12.0"],
                  "last_7d_mean": ["40.0", "10.0"]}).to_csv(paths.processed_dir / tm.FULL_REPORT_IN, index=False)


def run(paths, plots=False):
    setup(paths)
    assert tm.main(["--ks", "2,3"] + ([] if plots else ["--no-plots"]), paths=paths) == 0
    return {(r["product_id"], r["comment_id"]): r for r in read_csv(paths.processed_dir / tm.COMMENTS_OUT)}


def test_label_thresholds():
    assert tm.sentiment_label(0.05) == "positive"
    assert tm.sentiment_label(0.0499) == "neutral" == tm.sentiment_label(-0.0499) == tm.sentiment_label(0.0)
    assert tm.sentiment_label(-0.05) == "negative"
    assert tm.sentiment_label(float("nan")) == ""


def test_negation_scores_negative():
    frame = pd.DataFrame({"text_sentiment_ready": ["not good", "good", "the specs sheet"], "is_usable": "True",
                          **{col: "False" for col, _ in tm.FLAG_REASONS}})
    out = tm.score_sentiment(frame)
    assert list(out["sentiment_label"]) == ["negative", "positive", "neutral"]
    assert out["sentiment_compound"][0] < -0.05
    assert list(out["vader_no_sentiment_words"]) == ["False", "False", "True"]


def test_flagged_rows_stay_missing_not_zero(paths):
    rows = run(paths)
    emoji, hindi, short = rows[("P001", "f0")], rows[("P002", "f1")], rows[("P002", "c17")]
    for row in (emoji, hindi, short):
        assert row["sentiment_compound"] == "" and row["sentiment_label"] == ""
        assert row["vader_no_sentiment_words"] == "" and row["dominant_topic"] == ""
        assert row["aspect_camera"] == ""
    assert emoji["sentiment_skip_reason"] == "emoji_only;empty_after_clean;too_short"
    assert hindi["sentiment_skip_reason"] == "likely_non_english"
    assert short["sentiment_skip_reason"] == "too_short"
    negated = rows[("P001", "c6")]
    assert negated["sentiment_label"] == "negative" and negated["sentiment_skip_reason"] == ""
    assert rows[("P001", "c8")]["vader_no_sentiment_words"] == "True"


def test_aspects_drop_ambiguous_keywords():
    mentions = tm.aspect_mentions(pd.Series(["nice video", "camera zoom", "price worth", "buy"]))
    assert list(mentions["aspect_camera"]) == [False, True, False, False]
    assert list(mentions["aspect_price_value"]) == [False, False, True, False]


def test_topic_documents_drop_brand_and_generic_words():
    corpus = pd.DataFrame({"comment_id": ["a", "b", "a"], "product_id": ["P001", "P001", "P002"],
                           "text_clean": ["", "", ""], "is_usable": ["True", "True", "True"],
                           "tokens": ["iphone pro s24 video camera zoom", "phone bro battery x", "same"]})
    docs = tm.topic_documents(corpus, PROTECTED)
    assert docs.to_dict() == {"a": "camera zoom", "b": "battery"}  # one document per comment


def test_topic_outputs_have_expected_shapes(paths):
    rows = run(paths)
    selection = read_csv(paths.processed_dir / tm.MODEL_SELECTION_OUT)
    main_rows = [r for r in selection if r["vocabulary"] == "domain_stop_list_no_brand_terms"]
    assert {(r["method"], r["k"]) for r in main_rows} == {(m, k) for m in ("lda", "nmf") for k in ("2", "3")}
    assert sum(r["selected_for_method"] == "True" for r in main_rows) == 2
    assert {r["method"] for r in selection if r["vocabulary"] == "brand_and_model_terms_kept"} == {"lda", "nmf"}
    k = int(next(r["k"] for r in main_rows if r["method"] == "lda" and r["selected_for_method"] == "True"))

    terms = pd.DataFrame(read_csv(paths.processed_dir / tm.TOPIC_TERMS_OUT))
    lda = terms[terms["method"] == "lda"]
    assert len(lda) == k * tm.TOP_N_TERMS and set(lda["topic"]) == {str(t) for t in range(1, k + 1)}
    assert (lda["label_status"] == tm.LABEL_STATUS).all()

    modelled = [r for r in rows.values() if r["dominant_topic"]]
    assert modelled
    for r in modelled:
        assert math.isclose(sum(float(r[f"topic_{t}"]) for t in range(1, k + 1)), 1, abs_tol=1e-3)
    assert rows[("P001", "c0")]["topic_1"] == rows[("P002", "c0")]["topic_1"]  # shared comment, one document

    by_product = read_csv(paths.processed_dir / tm.TOPICS_BY_PRODUCT_OUT)
    assert [r["product_id"] for r in by_product] == ["P001", "P002"]
    assert {f"topic_{t}_share" for t in range(1, k + 1)} <= set(by_product[0])
    groups = {(r["group_type"], r["group"]): r for r in read_csv(paths.processed_dir / tm.BY_GROUP_OUT)}
    assert groups[("product_type", "RISING")]["scored_comments"] == "17"  # 16 unique + shared c0 counted once
    sentiment = {r["product_id"]: r for r in read_csv(paths.processed_dir / tm.SENTIMENT_BY_PRODUCT_OUT)}
    assert sentiment["P001"]["scored_comments"] == "9" and sentiment["P002"]["scored_comments"] == "9"
    link = read_csv(paths.processed_dir / tm.SIGNAL_LINK_OUT)
    assert link and all(r["n_products"] == "2" and r["spearman_rho"] == "" for r in link)  # too few to correlate


def test_spotcheck_has_empty_human_labels_and_is_not_overwritten(paths):
    run(paths)
    path = paths.processed_dir / tm.SPOTCHECK_OUT
    spot = read_csv(path)
    assert spot and all(r["human_label"] == "" for r in spot)
    assert list(spot[0]) == ["product_id", "comment_id", "text", "vader_label", "human_label"]
    assert len({r["comment_id"] for r in spot}) == len(spot)
    edited = pd.DataFrame(spot).assign(human_label=["positive"] + [""] * (len(spot) - 1))
    edited.to_csv(path, index=False, lineterminator="\n")
    before = path.read_bytes()
    assert tm.main(["--ks", "2", "--no-plots"], paths=paths) == 0
    assert path.read_bytes() == before


def test_rerun_is_deterministic_and_writes_only_outputs(paths):
    setup(paths)
    before = {p: p.read_bytes() for p in paths.root.rglob("*") if p.is_file()}
    assert tm.main(["--ks", "2,3"], paths=paths) == 0
    first = {p: p.read_bytes() for p in paths.root.rglob("*") if p.is_file()}
    expected = {paths.processed_dir / name for name in (
        tm.COMMENTS_OUT, tm.SENTIMENT_BY_PRODUCT_OUT, tm.TOPIC_TERMS_OUT, tm.TOPICS_BY_PRODUCT_OUT,
        tm.MODEL_SELECTION_OUT, tm.BY_GROUP_OUT, tm.SIGNAL_LINK_OUT, tm.SPOTCHECK_OUT)}
    expected.add(paths.root / "docs" / tm.SUMMARY_DOC)
    expected |= {paths.root / "docs" / "figures" / f for f in tm.FIGURES.values()}
    assert set(first) - set(before) == expected
    assert all(first[p] == data for p, data in before.items())  # inputs untouched
    assert tm.main(["--ks", "2,3"], paths=paths) == 0
    second = {p: p.read_bytes() for p in paths.root.rglob("*") if p.is_file()}
    assert second == first
    assert "Leakage: descriptive only" in (paths.root / "docs" / tm.SUMMARY_DOC).read_text(encoding="utf-8")


def test_missing_input_exits_1(paths):
    assert tm.main(["--no-plots"], paths=paths) == 1
