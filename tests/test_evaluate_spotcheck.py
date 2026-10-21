import pandas as pd
import pytest

from conftest import read_csv

pytest.importorskip("vaderSentiment")  # text_mining (imported below) needs VADER
import evaluate_spotcheck as ev  # noqa: E402
import text_mining as tm  # noqa: E402

SPOT = [  # comment_id, vader_label, ai_label, ai_note, compound
    ("a1", "positive", "positive", "", "0.6"),
    ("a2", "positive", "negative", "", "0.5"),
    ("a3", "neutral", "negative", "", "0.0"),  # VADER found no sentiment word
    ("a4", "neutral", "neutral", "", "0.0"),
    ("a5", "negative", "negative", "", "-0.6"),
    ("a6", "positive", "unclear", "Tamil; partly understood", "0.4"),  # excluded
    ("a7", "negative", "positive", "sarcastic praise", "-0.3"),
]


def setup(paths, doc="# Text mining\n\nGenerated text.\n"):
    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    spot = pd.DataFrame({"product_id": "P001", "comment_id": [r[0] for r in SPOT],
                         "text": [f"comment {r[0]}" for r in SPOT], "vader_label": [r[1] for r in SPOT],
                         "human_label": "", "ai_label": [r[2] for r in SPOT], "ai_note": [r[3] for r in SPOT],
                         "label_source": ev.LABEL_SOURCE})
    spot.to_csv(paths.processed_dir / tm.SPOTCHECK_OUT, index=False, lineterminator="\n")
    pd.DataFrame({"product_id": "P001", "comment_id": [r[0] for r in SPOT],
                  "sentiment_compound": [r[4] for r in SPOT]}).to_csv(paths.processed_dir / tm.COMMENTS_OUT,
                                                                       index=False)
    (paths.root / "docs").mkdir(exist_ok=True)
    (paths.root / "docs" / tm.SUMMARY_DOC).write_text(doc, encoding="utf-8")


def table(paths):
    return read_csv(paths.processed_dir / ev.EVAL_OUT)


def value(rows, section, metric, ai="", vader=""):
    [row] = [r for r in rows if r["section"] == section and r["metric"] == metric and r["ai_label"] == ai
             and r["vader_label"] == vader]
    return row["value"]


def test_agreement_and_confusion_on_known_sample(paths):
    setup(paths)
    assert ev.main([], paths=paths) == 0
    rows = table(paths)
    assert value(rows, "summary", "compared") == "6" and value(rows, "summary", "excluded_unclear") == "1"
    assert value(rows, "summary", "agreements") == "3" and float(value(rows, "summary", "agreement")) == 0.5
    low, high = float(value(rows, "summary", "agreement_ci95_low")), float(value(rows, "summary",
                                                                                "agreement_ci95_high"))
    assert low < 0.5 < high and high - low > 0.5  # n = 6: very wide
    expected = {("positive", "positive"): 1, ("positive", "negative"): 1, ("neutral", "neutral"): 1,
                ("negative", "positive"): 1, ("negative", "neutral"): 1, ("negative", "negative"): 1}
    for ai in ev.CLASSES:
        for vader in ev.CLASSES:
            assert value(rows, "confusion", "count", ai, vader) == str(expected.get((ai, vader), 0))
    assert float(value(rows, "per_class", "precision", "positive")) == 0.5
    assert float(value(rows, "per_class", "recall", "negative")) == pytest.approx(1 / 3, abs=1e-4)
    assert value(rows, "summary", "disagreements_vader_neutral_no_sentiment_words") == "1"
    assert [r["comment_id"] for r in rows if r["section"] == "disagreement"] == ["a2", "a7", "a3"]
    assert all(r["label_source"] == "AI review, not human" for r in rows)


def test_unclear_rows_are_excluded():
    spot = pd.DataFrame({"vader_label": ["positive", "negative"], "ai_label": ["positive", "unclear"],
                         "ai_note": "", "compound": [0.5, -0.5], "comment_id": ["x", "y"], "text": ""})
    res = ev.evaluate(spot)
    assert res["n"] == 1 and res["agreement"] == 1.0 and res["unclear"] == 1 and res["disagreements"] == 0


def test_spotcheck_and_human_label_untouched_and_doc_section_replaced(paths):
    setup(paths)
    spot_path = paths.processed_dir / tm.SPOTCHECK_OUT
    before = spot_path.read_bytes()
    assert ev.main([], paths=paths) == 0
    assert ev.main([], paths=paths) == 0
    assert spot_path.read_bytes() == before
    assert all(r["human_label"] == "" for r in read_csv(spot_path))
    doc = (paths.root / "docs" / tm.SUMMARY_DOC).read_text(encoding="utf-8")
    assert doc.startswith("# Text mining\n\nGenerated text.\n\n" + tm.EVAL_BLOCK_START)
    assert doc.count(tm.EVAL_BLOCK_START) == 1 and doc.endswith(tm.EVAL_BLOCK_END + "\n")
    assert "not human annotations" in doc and "human accuracy" not in doc.lower()


def test_text_mining_rerun_keeps_ai_labels_and_eval_section(paths):
    from test_text_mining import run as run_text_mining

    run_text_mining(paths)
    spot_path = paths.processed_dir / tm.SPOTCHECK_OUT
    spot = pd.read_csv(spot_path, dtype=str, keep_default_na=False)
    spot["ai_label"], spot["ai_note"], spot["label_source"] = "neutral", "", ev.LABEL_SOURCE
    spot.to_csv(spot_path, index=False, lineterminator="\n")
    assert ev.main([], paths=paths) == 0
    doc_path = paths.root / "docs" / tm.SUMMARY_DOC
    labelled, doc = spot_path.read_bytes(), doc_path.read_text(encoding="utf-8")
    assert tm.main(["--ks", "2,3", "--no-plots"], paths=paths) == 0
    assert spot_path.read_bytes() == labelled  # ai_label alone protects the file
    assert doc_path.read_text(encoding="utf-8") == doc  # the evaluation section survives the rerun


def test_missing_or_unlabelled_spotcheck_exits_1(paths):
    assert ev.main([], paths=paths) == 1
    setup(paths)
    spot_path = paths.processed_dir / tm.SPOTCHECK_OUT
    pd.read_csv(spot_path).drop(columns=["ai_label"]).to_csv(spot_path, index=False)
    assert ev.main([], paths=paths) == 1
