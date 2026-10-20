import csv

import build_text_corpus as tc
from conftest import read_csv

HEADER = ["snapshot_date", "product_id", "query_version", "video_id", "comment_id", "text", "like_count",
          "published_at", "is_emoji_only", "likely_non_english"]
COMMENTS = [
    # same comment in two snapshots: the 2026-10-03 copy (5 likes) must be kept
    ["2026-10-04", "P001", "v1", "vA", "k1", "Sooo GOOD!!!! not bad https://youtu.be/x?si=1 @TechyKiran",
     "9", "2026-10-01T10:00:00Z", "False", "False"],
    ["2026-10-03", "P001", "v1", "vA", "k1", "Sooo GOOD!!!! not bad https://youtu.be/x?si=1 @TechyKiran",
     "5", "2026-10-01T10:00:00Z", "False", "False"],
    # the same video sits in both products' results: kept once per product
    ["2026-10-04", "P002", "v1", "vA", "k1", "Sooo GOOD!!!! not bad https://youtu.be/x?si=1 @TechyKiran",
     "9", "2026-10-01T10:00:00Z", "False", "False"],
    ["2026-10-04", "P001", "v1", "vA", "k2", "🔥🔥", "0", "2026-10-02T10:00:00Z", "True", "False"],
    ["2026-10-04", "P002", "v1", "vC", "k3", "बहुत अच्छा फ़ोन है", "1", "2026-10-02T11:00:00Z", "False", "True"],
    ["2026-10-04", "P002", "v1", "vC", "k4", "", "0", "2026-10-02T12:00:00Z", "False", "False"],  # empty text
    ["2026-10-04", "P002", "v2", "vC", "k5", "pixel 11 from an old query", "0", "2026-10-02T12:00:00Z",
     "False", "False"],  # not the active query version (v1)
    ["2026-10-04", "P002", "v1", "vC", "k6", "Pixel 11 vs S25 Ultra: the 11 has 5000 mAh, see @7:28",
     "", "2026-10-02T13:00:00Z", "False", "False"],
]


def setup(paths):
    path = paths.processed_dir / tc.COMMENTS_IN
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([HEADER] + COMMENTS)


def run(paths, plots=False):
    setup(paths)
    assert tc.main([] if plots else ["--no-plots"], paths=paths) == 0
    return {(r["product_id"], r["comment_id"]): r for r in read_csv(paths.processed_dir / tc.CORPUS_OUT)}


def test_sentiment_text_lowercased_without_urls_mentions_and_repeats():
    text, counts = tc.sentiment_ready("Sooo GOOD!!!! &amp; <b>not</b> bad 😂😂😂 https://youtu.be/x @TechyKiran. @7:28")
    assert text == "soo good!! & not bad 😂😂 @7:28"
    assert counts["urls"] == 1 and counts["mentions"] == 1 and counts["html"] == 1


def test_negation_kept_in_sentiment_text_but_removed_from_text_clean(paths):
    row = run(paths)[("P001", "k1")]
    assert row["text_sentiment_ready"] == "soo good!! not bad"
    assert "not" not in row["text_clean"].split()
    assert row["text_clean"] == "soo good bad"
    assert row["text_raw"] == COMMENTS[1][5]  # untouched


def test_model_tokens_kept_and_other_digits_removed():
    protected = frozenset({"pixel", "11", "s25", "ultra", "nothing"})
    tokens = tc.topic_tokens(tc.sentiment_ready("Pixel 11 vs S25 Ultra or Nothing? 5000 mAh, don't care 7:28")[0],
                             protected)
    assert tokens == ["pixel", "11", "vs", "s25", "ultra", "nothing", "mah", "care"]


def test_protected_terms_come_from_the_registry(products):
    terms = tc.protected_terms(products)
    assert {"iphone", "18", "pro", "pixel", "11", "apple", "google"} <= terms


def test_dedup_keeps_earliest_snapshot_once_per_product(paths):
    rows = run(paths)
    assert rows[("P001", "k1")]["first_snapshot_date"] == "2026-10-03"
    assert rows[("P001", "k1")]["like_count"] == "5"
    assert rows[("P001", "k1")]["n_snapshots"] == "2"
    assert rows[("P001", "k1")]["n_products_sharing"] == "2" == rows[("P002", "k1")]["n_products_sharing"]
    assert ("P002", "k4") not in rows  # empty text dropped
    assert ("P002", "k5") not in rows  # inactive query version
    assert len(rows) == 5
    report = {r["step"]: r for r in read_csv(paths.processed_dir / tc.REPORT_OUT)}
    assert report["drop_duplicate_comments"]["rows_before"] == "6"
    assert report["drop_duplicate_comments"]["rows_after"] == "5"
    assert report["select_active_query"]["rows_after"] == "7"


def test_flags_kept_not_dropped(paths):
    rows = run(paths)
    emoji, hindi, long_ = rows[("P001", "k2")], rows[("P002", "k3")], rows[("P002", "k6")]
    assert emoji["is_emoji_only"] == "True" and emoji["is_empty_after_clean"] == "True"
    assert emoji["is_too_short"] == "True" and emoji["is_usable"] == "False"
    assert hindi["likely_non_english"] == "True" and hindi["is_usable"] == "False"
    assert hindi["text_clean"] == "बहुत अच्छा फ़ोन है"  # Indic words stay whole
    assert long_["is_usable"] == "True" and long_["like_count"] == ""  # missing stays missing


def test_by_product_covers_every_registry_product(paths):
    run(paths)
    by_product = {r["product_id"]: r for r in read_csv(paths.processed_dir / tc.BY_PRODUCT_OUT)}
    assert set(by_product) == {"P001", "P002"}
    assert by_product["P001"]["comments"] == "2" and by_product["P001"]["usable_comments"] == "1"
    assert by_product["P002"]["top_terms"].startswith("11:2; ")  # k1 (shared with P001) and k6 are usable
    assert {"pixel:1", "s25:1", "good:1"} <= set(by_product["P002"]["top_terms"].split("; "))


def test_rerun_is_idempotent_and_writes_docs(paths):
    run(paths, plots=True)
    outputs = [paths.processed_dir / name for name in (tc.CORPUS_OUT, tc.BY_PRODUCT_OUT, tc.REPORT_OUT)]
    outputs.append(paths.root / "docs" / tc.SUMMARY_DOC)
    first = [p.read_bytes() for p in outputs]
    run(paths, plots=True)
    assert [p.read_bytes() for p in outputs] == first
    assert "Leakage warning for SCRUM-24" in outputs[-1].read_text(encoding="utf-8")
    for fig in (tc.TOP_TERMS_FIG, tc.TOKENS_FIG):
        assert (paths.root / "docs" / "figures" / fig).stat().st_size > 0


def test_missing_input_exits_1(paths):
    assert tc.main(["--no-plots"], paths=paths) == 1
