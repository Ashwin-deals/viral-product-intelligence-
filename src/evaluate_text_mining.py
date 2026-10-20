"""Evaluate the SCRUM-28 sentiment against human labels, and check whether comments come before searches (SCRUM-29).

Part 1: is the sentiment right?
  Each of the 100 spot-check comments has a reference label (data/processed/text_sentiment_reference_labels.csv):
  some labelled by hand, the rest by a second AI review (Claude). label_source says which on every row,
  and neither saw VADER's or the SCRUM-28 AI review's labels (the note column marks any exception).
  Each method is scored against the reference on the same comments, once for all labels and once
  for the human labels only:
    - VADER            the project's method (labels from src/text_mining.py)
    - TextBlob         a second lexicon method, on the same text with the same +/-0.05 cut-offs
    - AI review        the AI labels of the SCRUM-28 spot check ("unclear" counts as a miss)
    - Always positive  a no-skill baseline: positive is the most common label in the scored corpus
  Comments whose reference label is "unclear" are left out. Metrics: accuracy with a 95% Wilson interval,
  macro F1, recall and precision for negative comments, Cohen's kappa.

Part 2: do comments come before searches?
  Weekly series per phone, full Monday-Sunday weeks inside the Trends window: mean search interest,
  comments posted, mean VADER score (only weeks with at least MIN_WEEK_COMMENTS scored comments).
  Each series is z-scored within the phone so that every phone counts on the same scale. For each lag
  k in LAGS, the Spearman correlation (pooled over all phones) between this week's change in search
  interest and the comment signal k weeks earlier. k > 0 means the comments come first.

Inputs (read only):
  data/processed/text_sentiment_reference_labels.csv  reference_label, label_source and note per comment
  data/processed/text_sentiment_spotcheck.csv     the 100 comments with VADER and AI-review labels (SCRUM-28)
  data/processed/text_corpus.csv                  text_sentiment_ready, the text VADER scored (SCRUM-23)
  data/processed/text_sentiment_comments.csv      VADER score per comment (SCRUM-28)
  processed/master_dataset.csv                    daily search interest and comment counts (SCRUM-21)
Outputs:
  data/processed/text_eval_method_comparison.csv  one row per reference set and method
  data/processed/text_eval_confusion.csv          reference label vs predicted label counts, per method
  data/processed/text_signal_lead_lag.csv         correlation per signal and lag

Usage: python src/evaluate_text_mining.py
"""

import sys

import pandas as pd
from scipy import stats
from sklearn.metrics import cohen_kappa_score, f1_score
from textblob import TextBlob

import config
from build_master import MASTER_OUT, local_day
from build_text_corpus import CORPUS_OUT
from text_mining import COMMENTS_OUT, SPOTCHECK_OUT, sentiment_label

REFERENCE_IN = "text_sentiment_reference_labels.csv"
COMPARISON_OUT = "text_eval_method_comparison.csv"
CONFUSION_OUT = "text_eval_confusion.csv"
LEAD_LAG_OUT = "text_signal_lead_lag.csv"

CLASSES = ["positive", "neutral", "negative"]
KEY = ["product_id", "comment_id"]
MIN_WEEK_COMMENTS = 5  # a weekly mean of fewer scored comments is too noisy to use
LAGS = range(-2, 3)


# ---- Part 1: methods vs human labels ---------------------------------------------------------

def load_labelled(paths):
    """The spot-check comments with the reference, VADER, TextBlob and AI-review labels side by side."""
    proc = paths.processed_dir
    reference = pd.read_csv(proc / REFERENCE_IN, dtype=str, keep_default_na=False)
    # label_source there only says that ai_label is an AI review; here it means the reference label's source
    spot = pd.read_csv(proc / SPOTCHECK_OUT, dtype=str, keep_default_na=False).drop(columns=["human_label",
                                                                                               "label_source"])
    corpus = pd.read_csv(proc / CORPUS_OUT, usecols=KEY + ["text_sentiment_ready"], dtype=str,
                         keep_default_na=False).drop_duplicates(KEY)
    data = (spot.merge(reference.rename(columns={"note": "reference_note"}), on=KEY, how="left",
                       validate="one_to_one")
                .merge(corpus, on=KEY, how="left", validate="one_to_one"))
    data["textblob_label"] = [sentiment_label(TextBlob(t).sentiment.polarity) for t in data["text_sentiment_ready"]]
    return data


def score(truth, predicted):
    n = len(truth)
    correct = int((truth == predicted).sum())
    ci = stats.binomtest(correct, n).proportion_ci(confidence_level=0.95, method="wilson")
    actual_neg, said_neg = truth == "negative", predicted == "negative"
    hit_neg = int((actual_neg & said_neg).sum())
    return {"comments": n, "accuracy": correct / n, "accuracy_ci95_low": ci.low, "accuracy_ci95_high": ci.high,
            "macro_f1": f1_score(truth, predicted, labels=CLASSES, average="macro", zero_division=0),
            "negative_recall": hit_neg / actual_neg.sum() if actual_neg.any() else float("nan"),
            "negative_precision": hit_neg / said_neg.sum() if said_neg.any() else float("nan"),
            "cohen_kappa": cohen_kappa_score(truth, predicted)}


def compare_methods(data, reference):
    """Score each method against data's reference_label; `reference` names that label set in the output."""
    rated = data[data["reference_label"].isin(CLASSES)]
    if rated.empty:
        return pd.DataFrame(), pd.DataFrame()
    predictions = {"VADER (project method)": rated["vader_label"],
                   "TextBlob": rated["textblob_label"],
                   "AI review (SCRUM-28 spot check)": rated["ai_label"],
                   "Always positive (baseline)": pd.Series("positive", index=rated.index)}
    table = pd.DataFrame([{"reference": reference, "method": m, **score(rated["reference_label"], p)}
                          for m, p in predictions.items()])
    confusion = (pd.concat([pd.DataFrame({"reference": reference, "method": m,
                                          "reference_label": rated["reference_label"], "predicted": p})
                            for m, p in predictions.items()])
                 .value_counts().rename("comments").reset_index()
                 .sort_values(["method", "reference_label", "predicted"]))
    return table, confusion


# ---- Part 2: do comments come before searches? ------------------------------------------------

def week_start(dates):
    return pd.to_datetime(dates).dt.to_period("W-SUN").dt.start_time  # the Monday of each week


def weekly_signals(master, comments):
    days = master[["product_id", "date", "search_interest", "comments_published"]].copy()
    days["week"] = week_start(days["date"])
    weekly = days.groupby(["product_id", "week"]).agg(days=("date", "size"), search=("search_interest", "mean"),
                                                      comments=("comments_published", "sum"))
    weekly = weekly[weekly["days"] == 7]  # drop the part weeks at both ends of the window

    scored = comments.dropna(subset=["sentiment_compound"]).drop_duplicates(KEY).copy()
    scored["week"] = week_start(local_day(scored["published_at"]))
    sentiment = scored.groupby(["product_id", "week"]).agg(scored_comments=("sentiment_compound", "size"),
                                                           sentiment=("sentiment_compound", "mean"))
    weekly = weekly.join(sentiment, how="left")
    weekly["scored_comments"] = weekly["scored_comments"].fillna(0).astype(int)
    weekly.loc[weekly["scored_comments"] < MIN_WEEK_COMMENTS, "sentiment"] = float("nan")
    return weekly.reset_index()


def zscore(series):
    spread = series.std()
    return (series - series.mean()) / spread if spread else series * float("nan")


def lead_lag(weekly, lags=LAGS):
    w = weekly.sort_values(["product_id", "week"]).copy()
    by_phone = w.groupby("product_id")
    w["search_change"] = by_phone["search"].diff()
    w["comments_change"] = by_phone["comments"].diff()
    for col in ("search_change", "comments_change", "sentiment"):
        w[col] = w.groupby("product_id")[col].transform(zscore)

    rows = []
    for signal, meaning in (("comments_change", "change in comments posted"), ("sentiment", "mean VADER score")):
        for k in lags:
            earlier = w.groupby("product_id")[signal].shift(k)  # the signal k weeks before (after, if k < 0)
            pair = pd.DataFrame({"x": earlier, "y": w["search_change"]}).dropna()
            rho, p = stats.spearmanr(pair["x"], pair["y"]) if len(pair) > 2 else (float("nan"), float("nan"))
            rows.append({"signal": signal, "meaning": meaning, "lag_weeks": k, "spearman_rho": rho,
                         "p_value": p, "pairs": len(pair), "phones": w.loc[pair.index, "product_id"].nunique()})
    return pd.DataFrame(rows)


# ---- Run ------------------------------------------------------------------------------------

def main(paths=config.PATHS):
    proc = paths.processed_dir
    needed = [proc / REFERENCE_IN, proc / SPOTCHECK_OUT, proc / CORPUS_OUT, proc / COMMENTS_OUT,
              paths.root / "processed" / MASTER_OUT]
    absent = [str(p) for p in needed if not p.exists()]
    if absent:
        print(f"Missing input(s): {', '.join(absent)}", file=sys.stderr)
        return 1

    labelled = load_labelled(paths)
    parts = [compare_methods(labelled, "all labels (human + AI review)"),
             compare_methods(labelled[labelled["label_source"] == "human"], "human labels only")]
    table = pd.concat([t for t, _ in parts], ignore_index=True)
    confusion = pd.concat([c for _, c in parts], ignore_index=True)
    master = pd.read_csv(paths.root / "processed" / MASTER_OUT,
                         usecols=["product_id", "date", "search_interest", "comments_published"])
    comments = pd.read_csv(proc / COMMENTS_OUT, usecols=KEY + ["published_at", "sentiment_compound"],
                           dtype={"comment_id": str})
    lags = lead_lag(weekly_signals(master, comments))

    table.round(4).to_csv(proc / COMPARISON_OUT, index=False, lineterminator="\n")
    confusion.to_csv(proc / CONFUSION_OUT, index=False, lineterminator="\n")
    lags.round(4).to_csv(proc / LEAD_LAG_OUT, index=False, lineterminator="\n")
    print(table[["reference", "method", "comments", "accuracy", "macro_f1", "negative_recall", "cohen_kappa"]]
          .round(3).to_string(index=False))
    print()
    print(lags[["signal", "lag_weeks", "spearman_rho", "p_value", "pairs"]].round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
