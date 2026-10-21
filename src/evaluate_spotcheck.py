"""Compare VADER's labels with the AI-review labels of the sentiment spot check (SCRUM-28).

The labels in ai_label come from an AI review of each comment's text, not from human annotators;
label_source says so on every row. human_label is left empty for anyone who wants to add real
labels, and this script never writes to the spot-check file. The result is agreement between VADER
and an AI reader, not accuracy against human judgement.

Inputs (read only):
  data/processed/text_sentiment_spotcheck.csv  100 comments: vader_label, human_label, ai_label, ai_note,
                                               label_source (src/text_mining.py draws the sample)
  data/processed/text_sentiment_comments.csv   VADER compound per comment (for the "no sentiment words" share)
Outputs:
  data/processed/text_sentiment_spotcheck_eval.csv  long table: summary, label counts, confusion matrix,
                                                    precision/recall per class, the 10 clearest disagreements
  docs/text_mining.md                               the "Spot check" section, between the markers
                                                    text_mining.EVAL_BLOCK_START/END (kept on its reruns)

Rows whose ai_label is "unclear" (non-English the reviewer could not read, spam, off-topic) are
excluded. Agreement has a 95% Wilson interval: with about 90 rows it is roughly +/-10 points wide.
Precision and recall are VADER's, taking the AI review as the reference. "Clearest" disagreements:
opposite polarity first, then rows the reviewer was sure about (no ai_note), then larger |compound|.

Usage: python src/evaluate_spotcheck.py
"""

import argparse
import sys

import pandas as pd
from scipy import stats

import config
from text_mining import COMMENTS_OUT, EVAL_BLOCK_END, EVAL_BLOCK_START, SPOTCHECK_OUT, SUMMARY_DOC

EVAL_OUT = "text_sentiment_spotcheck_eval.csv"
CLASSES = ("positive", "neutral", "negative")
LABEL_SOURCE = "AI review, not human"
N_DISAGREEMENTS = 10
EVAL_COLUMNS = ["section", "metric", "ai_label", "vader_label", "value", "comment_id", "text", "label_source"]


def load(paths):
    spot = pd.read_csv(paths.processed_dir / SPOTCHECK_OUT, dtype=str, keep_default_na=False)
    comments_path = paths.processed_dir / COMMENTS_OUT
    if comments_path.exists():
        scores = pd.read_csv(comments_path, dtype=str, keep_default_na=False,
                             usecols=["product_id", "comment_id", "sentiment_compound"])
        spot = spot.merge(scores.drop_duplicates(["product_id", "comment_id"]), on=["product_id", "comment_id"],
                          how="left")
    else:
        spot["sentiment_compound"] = ""
    spot["compound"] = pd.to_numeric(spot["sentiment_compound"], errors="coerce")
    return spot


def evaluate(spot):
    compared = spot[spot["ai_label"].isin(CLASSES)].copy()
    n = len(compared)
    agree = int((compared["ai_label"] == compared["vader_label"]).sum())
    ci = stats.binomtest(agree, n).proportion_ci(confidence_level=0.95, method="wilson") if n else None
    confusion = pd.crosstab(compared["ai_label"], compared["vader_label"]).reindex(
        index=list(CLASSES), columns=list(CLASSES), fill_value=0)
    per_class = {}
    for c in CLASSES:
        predicted, actual = confusion[c].sum(), confusion.loc[c].sum()
        per_class[c] = {"precision": confusion.loc[c, c] / predicted if predicted else float("nan"),
                        "recall": confusion.loc[c, c] / actual if actual else float("nan"), "support": int(actual)}
    wrong = compared[compared["ai_label"] != compared["vader_label"]].copy()
    no_words = wrong["vader_label"].eq("neutral") & wrong["compound"].eq(0)
    wrong["opposite"] = wrong[["ai_label", "vader_label"]].apply(set, axis=1) == {"positive", "negative"}
    wrong["sure"] = wrong["ai_note"].str.strip() == ""
    wrong["strength"] = wrong["compound"].abs().fillna(0)
    clearest = wrong.sort_values(["opposite", "sure", "strength"], ascending=False, kind="stable").head(N_DISAGREEMENTS)
    return {"rows": len(spot), "unclear": int((spot["ai_label"] == "unclear").sum()), "n": n, "agree": agree,
            "agreement": agree / n if n else float("nan"), "ci": ci, "confusion": confusion, "per_class": per_class,
            "disagreements": len(wrong), "no_words": int(no_words.sum()),
            "label_counts": spot["ai_label"].value_counts().reindex([*CLASSES, "unclear"], fill_value=0),
            "clearest": clearest}


def eval_table(res):
    rows = []

    def add(section, metric, value, ai="", vader="", comment_id="", text=""):
        rows.append({"section": section, "metric": metric, "ai_label": ai, "vader_label": vader, "value": value,
                     "comment_id": comment_id, "text": text, "label_source": LABEL_SOURCE})

    add("note", "labels", "ai_label is an AI review of the comment text, not human annotation; "
                          "human_label is left empty for real labels")
    for metric, value in (("rows_in_spotcheck", res["rows"]), ("excluded_unclear", res["unclear"]),
                          ("compared", res["n"]), ("agreements", res["agree"]),
                          ("agreement", round(res["agreement"], 4)),
                          ("agreement_ci95_low", round(res["ci"].low, 4) if res["ci"] else ""),
                          ("agreement_ci95_high", round(res["ci"].high, 4) if res["ci"] else ""),
                          ("disagreements", res["disagreements"]),
                          ("disagreements_vader_neutral_no_sentiment_words", res["no_words"]),
                          ("share_of_disagreements_vader_no_sentiment_words",
                           round(res["no_words"] / res["disagreements"], 4) if res["disagreements"] else "")):
        add("summary", metric, value)
    for label, count in res["label_counts"].items():
        add("ai_label_counts", "count", int(count), ai=label)
    for ai in CLASSES:
        for vader in CLASSES:
            add("confusion", "count", int(res["confusion"].loc[ai, vader]), ai=ai, vader=vader)
    for c, m in res["per_class"].items():
        for metric in ("precision", "recall", "support"):
            add("per_class", metric, "" if pd.isna(m[metric]) else round(float(m[metric]), 4), ai=c)
    for rank, r in enumerate(res["clearest"].itertuples(), start=1):
        add("disagreement", f"rank_{rank}", "" if pd.isna(r.compound) else r.compound, ai=r.ai_label,
            vader=r.vader_label, comment_id=r.comment_id, text=r.text)
    return pd.DataFrame(rows, columns=EVAL_COLUMNS)


def _cell(text, limit=140):
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _pct(x):
    return "" if pd.isna(x) else f"{x:.0%}"


def eval_markdown(res):
    counts = res["label_counts"]
    lo, hi = (res["ci"].low, res["ci"].high) if res["ci"] else (float("nan"), float("nan"))
    lines = [EVAL_BLOCK_START, "## Spot check: VADER vs an AI review (not human labels)", "",
             f"**Read this first:** the reference labels (`ai_label` in `data/processed/{SPOTCHECK_OUT}`) come from "
             "an AI review that read each comment's text, without seeing VADER's label; they are **not human "
             "annotations**, and `label_source` says so on every row. `human_label` is left empty for anyone who "
             "wants to add real labels. The numbers below are agreement between VADER and that AI reader, not "
             "VADER's accuracy against human judgement.", "",
             "Rules used by the review: positive or negative for a clear tone about the phone, brand, review or "
             "reviewer; neutral for questions, requests and facts; the dominant tone for mixed comments; unclear "
             "for comments it could not read reliably and off-topic ones (greetings, jokes, spam). Hinglish and "
             "other romanised languages were labelled when the meaning was clear; `ai_note` marks every judgement "
             "call.", "",
             f"AI labels on the {res['rows']} sampled comments: {counts['positive']} positive, {counts['neutral']} "
             f"neutral, {counts['negative']} negative, {counts['unclear']} unclear (excluded).", "",
             "| | value |", "|---|--:|",
             f"| comments compared | {res['n']} |",
             f"| VADER agrees with the AI review | {res['agree']} = **{_pct(res['agreement'])}** |",
             f"| 95% confidence interval (Wilson) | {_pct(lo)} to {_pct(hi)} |",
             f"| disagreements | {res['disagreements']} |",
             f"| of which VADER said neutral with compound exactly 0 (no sentiment word found) | {res['no_words']} "
             f"= {_pct(res['no_words'] / res['disagreements'] if res['disagreements'] else float('nan'))} |", "",
             f"With n = {res['n']} the interval is wide (about ±{(hi - lo) / 2:.0%}): treat the agreement as a rough "
             "level, not a precise score.", "",
             "Confusion matrix (rows: AI review, columns: VADER):", "",
             "| AI review \\ VADER | " + " | ".join(CLASSES) + " | total |", "|---|" + "--:|" * (len(CLASSES) + 1)]
    for ai in CLASSES:
        row = res["confusion"].loc[ai]
        lines.append(f"| {ai} | " + " | ".join(str(int(row[c])) for c in CLASSES) + f" | {int(row.sum())} |")
    lines += ["", "VADER's precision and recall per class, taking the AI review as the reference:", "",
              "| class | precision | recall | AI-labelled comments |", "|---|--:|--:|--:|"]
    for c, m in res["per_class"].items():
        lines.append(f"| {c} | {_pct(m['precision'])} | {_pct(m['recall'])} | {m['support']} |")
    lines += ["", f"The {len(res['clearest'])} clearest disagreements (opposite polarity first, then comments the "
                  "review was sure about):", "",
              "| comment | VADER | AI review | AI note |", "|---|---|---|---|"]
    for r in res["clearest"].itertuples():
        compound = "" if pd.isna(r.compound) else f" ({r.compound:+.2f})"
        lines.append(f"| {_cell(r.text)} | {r.vader_label}{compound} | {r.ai_label} | {_cell(r.ai_note, 60)} |")
    lines += ["", f"Full table: `data/processed/{EVAL_OUT}` (`python3 src/evaluate_spotcheck.py`).", EVAL_BLOCK_END]
    return "\n".join(lines)


def write_doc_section(path, block):
    """Replace the section between the markers, or append it; the rest of the doc is untouched."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    start, end = text.find(EVAL_BLOCK_START), text.find(EVAL_BLOCK_END)
    if 0 <= start < end:
        text = text[:start] + text[end + len(EVAL_BLOCK_END):]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + "\n\n" + block + "\n", encoding="utf-8")


def main(argv=None, paths=config.PATHS):
    argparse.ArgumentParser(description="Compare VADER with the AI-review spot-check labels (SCRUM-28).") \
        .parse_args(argv)
    spot_path = paths.processed_dir / SPOTCHECK_OUT
    if not spot_path.exists():
        print(f"Missing {spot_path}. Run src/text_mining.py first.", file=sys.stderr)
        return 1
    spot = load(paths)
    if "ai_label" not in spot or not spot["ai_label"].isin(CLASSES).any():
        print(f"No ai_label to compare in {spot_path}.", file=sys.stderr)
        return 1
    res = evaluate(spot)
    eval_table(res).to_csv(paths.processed_dir / EVAL_OUT, index=False, lineterminator="\n")
    write_doc_section(paths.root / "docs" / SUMMARY_DOC, eval_markdown(res))
    print(f"compared {res['n']} (excluded {res['unclear']} unclear): VADER agrees with the AI review on "
          f"{res['agree']} = {res['agreement']:.1%} (95% CI {res['ci'].low:.1%} to {res['ci'].high:.1%}); "
          f"{res['no_words']} of {res['disagreements']} disagreements are VADER neutral with no sentiment words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
