"""Build the YouTube comment text corpus (SCRUM-23). Inputs are read, never modified.

Input:   data/processed/youtube_comments_clean.csv  (src/clean_youtube.py)
         active registry (config.ACTIVE_REGISTRY)     brand/model words to protect, product fields
Outputs: data/processed/text_corpus.csv             one row per unique comment of a product
         data/processed/text_corpus_by_product.csv  one row per registry product
         data/processed/text_cleaning_report.csv    step-by-step counts
         docs/text_corpus.md                        method, counts, leakage warning (generated)
         docs/figures/text_top_terms.png, docs/figures/text_tokens_per_comment.png

Extraction:
- Active query only: rows whose query_version is not the product's active one are excluded, as in
  clean_youtube.py (the cleaned table already holds only active rows; this re-checks it).
- Rows whose text is empty before cleaning are dropped (the only rows ever dropped).
- One row per unique comment of a product: the key is (product_id, comment_id), or
  (product_id, video_id, text) when a row has no comment_id. A comment seen in several snapshots
  keeps its earliest snapshot (first_snapshot_date, like_count from that snapshot); n_snapshots
  counts the snapshots. The same comment can also belong to several products (one video can be
  in several products' search results); it is kept once per product and n_products_sharing says
  how many products share it. Deduplicating on comment_id alone would drop a whole product.
- text_raw is the cleaned table's text, unchanged.

Cleaning:
- text_sentiment_ready (light, for sentiment): HTML entities and tags removed, lowercased, URLs
  and @mentions removed (an @ followed by a letter; "@7:28" timestamps are kept), any character
  other than a digit or space repeated 3+ times cut to 2 ("sooo" -> "soo", "!!!!" -> "!!"),
  whitespace collapsed. Stop-words, negations, punctuation and emojis are kept.
- text_clean (full, for topic modelling): from text_sentiment_ready, apostrophes joined ("don't" ->
  "dont"), video timestamps ("7:28") removed, then tokenized; emojis and punctuation fall away
  because the tokenizer keeps only runs of letters and digits (Latin and Indic scripts). Tokens
  that are only digits and stop-words (config.TEXT_EXTRA_STOP_WORDS plus sklearn's
  ENGLISH_STOP_WORDS) are removed, except registry brand and model tokens ("nothing", "15",
  "s25", "pro", "ultra"), which are always kept. tokens is text_clean split into those tokens
  (the same words; it exists so later steps can split on spaces), n_tokens their count.
- Flags, rows kept: is_emoji_only and likely_non_english (from clean_youtube.py),
  is_empty_after_clean (no token left), is_too_short (fewer than config.TEXT_MIN_TOKENS tokens,
  empty included). is_usable = none of the four. The model step filters on these flags.

The CSV outputs depend only on the inputs, so re-running gives identical files.

Usage: python src/build_text_corpus.py [--no-plots]
"""

import argparse
import html
import re
import sys
from collections import Counter

import pandas as pd
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

import config
from clean_youtube import active_query_versions
from common import read_products

COMMENTS_IN = "youtube_comments_clean.csv"
CORPUS_OUT = "text_corpus.csv"
BY_PRODUCT_OUT = "text_corpus_by_product.csv"
REPORT_OUT = "text_cleaning_report.csv"
SUMMARY_DOC = "text_corpus.md"
TOP_TERMS_FIG = "text_top_terms.png"
TOKENS_FIG = "text_tokens_per_comment.png"

CORPUS_COLUMNS = [
    "product_id", "comment_id", "video_id", "query_version", "first_snapshot_date", "n_snapshots",
    "n_products_sharing", "published_at", "like_count", "text_raw", "text_sentiment_ready", "text_clean",
    "tokens", "n_tokens", "is_emoji_only", "likely_non_english", "is_empty_after_clean", "is_too_short",
    "is_usable",
]
BY_PRODUCT_COLUMNS = ["product_id", "product_name", "brand", "product_type", "comments", "usable_comments",
                      "usable_share", "tokens_total", "tokens_usable", "mean_tokens_per_comment", "top_terms"]
REPORT_HEADER = ["step", "rows_before", "rows_after", "rows_flagged", "detail"]
TOP_TERMS_PER_PRODUCT = 10
TOP_TERMS_FIGURE = 20

STOP_WORDS = frozenset(ENGLISH_STOP_WORDS).union(*config.TEXT_EXTRA_STOP_WORDS.values())

HTML_TAG_PATTERN = re.compile(r"</?[A-Za-z][^>]*>")
HTML_ENTITY_PATTERN = re.compile(r"&(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);")  # entities left after unescaping
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+")
MENTION_PATTERN = re.compile(r"(?<![\w@])@[a-z][\w.\-]*")
REPEAT_PATTERN = re.compile(r"([^\d\s])\1{2,}")
WHITESPACE_PATTERN = re.compile(r"\s+")
APOSTROPHE_PATTERN = re.compile(r"['’`]")
TIMESTAMP_PATTERN = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
# Letters and digits in any script, plus the Indic blocks (Devanagari to Sinhala, U+0900-U+0DFF)
# so vowel signs stay inside their word; the danda punctuation (U+0964-U+0965) splits words.
TOKEN_PATTERN = re.compile(r"(?:[^\W_]|[ऀ-ॣ०-෿])+")


def tokenize(text):
    return TOKEN_PATTERN.findall(text)


def protected_terms(registry):
    """Brand and model tokens from the registry; never removed from text_clean."""
    terms = set()
    for p in registry:
        for col in ("brand", "product_name", "trends_query"):
            terms.update(tokenize(p[col].lower()))
    return frozenset(terms)


def sentiment_ready(text):
    """Light clean. Returns (text, {step: items removed or changed})."""
    counts = {}
    unescaped = HTML_ENTITY_PATTERN.sub(" ", HTML_TAG_PATTERN.sub(" ", html.unescape(text)))
    counts["html"] = int(unescaped != text)
    text = unescaped.lower()
    text, counts["urls"] = URL_PATTERN.subn(" ", text)
    text, counts["mentions"] = MENTION_PATTERN.subn(" ", text)
    text, counts["repeats"] = REPEAT_PATTERN.subn(r"\1\1", text)
    return WHITESPACE_PATTERN.sub(" ", text).strip(), counts


def topic_tokens(text, protected=frozenset(), stop_words=STOP_WORDS):
    """Full clean of a sentiment-ready text: tokens without digits-only tokens and stop-words."""
    text = TIMESTAMP_PATTERN.sub(" ", APOSTROPHE_PATTERN.sub("", text))
    return [t for t in tokenize(text)
            if t in protected or not (t.isdigit() or t in stop_words)]


def is_true(series):
    return series.astype(str).str.strip().str.lower().isin({"true", "1"})


def build_corpus(comments, active_versions, protected):
    """(corpus, report rows) from the cleaned comments table."""
    report = []

    def step(name, before, after, flagged="", detail=""):
        report.append({"step": name, "rows_before": before, "rows_after": after, "rows_flagged": flagged,
                       "detail": detail})

    df = comments.copy()
    step("load", len(df), len(df), detail=f"rows read from data/processed/{COMMENTS_IN}")

    before = len(df)
    active = df["product_id"].map(active_versions)
    excluded = df[df["query_version"] != active]
    df = df[df["query_version"] == active]
    detail = "; ".join(f"{pid} {qv}: {n}" for (pid, qv), n in
                       excluded.groupby(["product_id", "query_version"]).size().items())
    step("select_active_query", before, len(df), detail=f"rows not from the active query version: {detail or 'none'}")

    before = len(df)
    df = df[df["text"].astype(str).str.strip() != ""]
    step("drop_empty_text", before, len(df), detail="text empty before cleaning (the only rows dropped)")

    before = len(df)
    df = df.assign(dedup_id=df["comment_id"].where(df["comment_id"].str.strip() != "",
                                                   "noid:" + df["video_id"] + "|" + df["text"]))
    df = df.sort_values("snapshot_date", kind="stable")
    key = ["product_id", "dedup_id"]
    n_snapshots = df.groupby(key)["snapshot_date"].nunique().rename("n_snapshots")
    df = df.drop_duplicates(key, keep="first").merge(n_snapshots, on=key)
    with_id = df["comment_id"].str.strip() != ""
    sharing = df[with_id].groupby("comment_id")["product_id"].nunique()
    df["n_products_sharing"] = df["comment_id"].map(sharing).where(with_id, 1).astype(int)
    shared = df[df["n_products_sharing"] > 1]
    step("drop_duplicate_comments", before, len(df),
         detail=f"key (product_id, comment_id): copies from later snapshots removed, earliest kept; "
                f"{int((~with_id).sum())} row(s) without comment_id keyed on (product_id, video_id, text). "
                f"Kept once per product: {shared['comment_id'].nunique()} comment ids appear under 2+ products "
                f"({len(shared)} rows, see n_products_sharing)")

    cleaned = df["text"].map(sentiment_ready)
    df["text_sentiment_ready"] = cleaned.str[0]
    counts = pd.DataFrame(cleaned.str[1].tolist(), index=df.index)
    for name, label, what in (("urls", "remove_urls", "URLs"), ("mentions", "remove_mentions", "@mentions"),
                              ("html", "remove_html_leftovers", "comments changed"),
                              ("repeats", "collapse_repeated_characters", "runs collapsed")):
        flagged = int((counts[name] > 0).sum())
        extra = f"; {int(counts[name].sum())} {what} removed" if name in ("urls", "mentions") else \
            (f"; {int(counts[name].sum())} {what}" if name == "repeats" else "")
        step(label, len(df), len(df), flagged, f"comments affected{extra}")

    token_lists = df["text_sentiment_ready"].map(lambda t: topic_tokens(t, protected))
    df["text_clean"] = token_lists.map(" ".join)
    df["tokens"] = df["text_clean"]
    df["n_tokens"] = token_lists.map(len)
    df["is_emoji_only"] = is_true(df["is_emoji_only"])
    df["likely_non_english"] = is_true(df["likely_non_english"])
    df["is_empty_after_clean"] = df["n_tokens"] == 0
    df["is_too_short"] = df["n_tokens"] < config.TEXT_MIN_TOKENS
    df["is_usable"] = ~(df["is_emoji_only"] | df["likely_non_english"] | df["is_empty_after_clean"]
                        | df["is_too_short"])
    for flag, detail in (("is_empty_after_clean", "no token left after full cleaning"),
                         ("is_too_short", f"fewer than {config.TEXT_MIN_TOKENS} tokens (empty included)"),
                         ("is_emoji_only", "from clean_youtube.py"),
                         ("likely_non_english", "from clean_youtube.py (script heuristic; Hinglish counts as English)"),
                         ("is_usable", "none of the four flags above")):
        step(f"flag_{flag}", len(df), len(df), int(df[flag].sum()), f"flagged, kept: {detail}")

    df = df.rename(columns={"text": "text_raw", "snapshot_date": "first_snapshot_date"})
    df["like_count"] = pd.to_numeric(df["like_count"], errors="coerce").astype("Int64")
    df = df.sort_values(["product_id", "comment_id", "dedup_id"], kind="stable").reset_index(drop=True)
    step("output", len(comments), len(df), detail=f"{df['product_id'].nunique()} products")
    return df[CORPUS_COLUMNS], report


def term_counts(token_strings):
    counts = Counter()
    for s in token_strings:
        counts.update(s.split())
    return counts


def top_terms_overall(corpus, n):
    """Most frequent terms in usable comments; a comment shared by several products counts once."""
    usable = corpus[corpus["is_usable"]]
    once = pd.concat([usable[usable["comment_id"] == ""],
                      usable[usable["comment_id"] != ""].drop_duplicates("comment_id")])
    return term_counts(once["tokens"]).most_common(n)


def by_product(corpus, registry):
    rows = []
    for p in registry:
        g = corpus[corpus["product_id"] == p["product_id"]]
        usable = g[g["is_usable"]]
        top = term_counts(usable["tokens"]).most_common(TOP_TERMS_PER_PRODUCT)
        rows.append({
            "product_id": p["product_id"], "product_name": p["product_name"], "brand": p["brand"],
            "product_type": p["product_type"], "comments": len(g), "usable_comments": len(usable),
            "usable_share": round(len(usable) / len(g), 4) if len(g) else None,
            "tokens_total": int(g["n_tokens"].sum()), "tokens_usable": int(usable["n_tokens"].sum()),
            "mean_tokens_per_comment": round(float(g["n_tokens"].mean()), 2) if len(g) else None,
            "top_terms": "; ".join(f"{t}:{c}" for t, c in top),
        })
    return pd.DataFrame(rows, columns=BY_PRODUCT_COLUMNS)


# Single-series charts: one blue (reference palette series-1), ink for text, recessive grid.
SURFACE, INK, INK_2, BLUE, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#2a78d6", "#e6e5e1"


def _style(ax, fig):
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_2)
    ax.tick_params(colors=INK_2, labelsize=10)
    ax.set_axisbelow(True)


def plot_top_terms(terms, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels, values = [t for t, _ in terms][::-1], [c for _, c in terms][::-1]
    fig, ax = plt.subplots(figsize=(9, 7.5))
    _style(ax, fig)
    ax.barh(labels, values, height=0.6, color=BLUE)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    for y, v in enumerate(values):
        ax.text(v + max(values) * 0.01, y, f"{v:,}", va="center", fontsize=9, color=INK_2)
    ax.set_xlim(0, max(values) * 1.1)
    ax.tick_params(axis="y", labelsize=11, colors=INK)
    ax.set_title(f"Top {len(terms)} terms in YouTube comments (topic-model text)", loc="left",
                 fontsize=13, fontweight="bold", color=INK)
    ax.set_xlabel("Occurrences in usable comments (a comment shared by several products counts once)",
                  fontsize=10, color=INK_2)
    fig.tight_layout()
    fig.savefig(path, dpi=150, metadata={"Software": None})
    plt.close(fig)


def plot_tokens_per_comment(n_tokens, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cap = int(n_tokens.quantile(0.99))
    shown = n_tokens[n_tokens <= cap]
    fig, ax = plt.subplots(figsize=(9, 5))
    _style(ax, fig)
    ax.hist(shown, bins=[b - 0.5 for b in range(0, cap + 2)], color=BLUE, edgecolor=SURFACE, linewidth=1.5)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    for value, name, style in ((n_tokens.median(), "median", "--"), (n_tokens.mean(), "mean", ":")):
        ax.axvline(value, color=INK, linestyle=style, linewidth=1.2)
        ax.text(value + 0.3, ax.get_ylim()[1] * (0.92 if name == "median" else 0.82),
                f"{name} {value:.1f}", fontsize=10, color=INK)
    ax.axvline(config.TEXT_MIN_TOKENS - 0.5, color=INK_2, linewidth=0.8)
    ax.set_title("Tokens per comment after full cleaning (text_clean)", loc="left", fontsize=13,
                 fontweight="bold", color=INK, pad=24)
    ax.text(0, 1.02, f"Bars left of the gray line (fewer than {config.TEXT_MIN_TOKENS} tokens) are flagged "
            "is_too_short; they are kept in the corpus", transform=ax.transAxes, fontsize=10, color=INK_2)
    ax.set_xlabel(f"Tokens per comment (axis cut at the 99th percentile, {cap}; "
                  f"{int((n_tokens > cap).sum())} longer comments not shown)", fontsize=10, color=INK_2)
    ax.set_ylabel("Comments", fontsize=10, color=INK_2)
    fig.tight_layout()
    fig.savefig(path, dpi=150, metadata={"Software": None})
    plt.close(fig)


def summary_markdown(corpus, by_prod, report, top15):
    steps = {r["step"]: r for r in report}
    n = len(corpus)

    def share(flag):
        k = int(corpus[flag].sum())
        return f"{k:,} ({k / n:.1%})" if n else "0"

    thin = by_prod[by_prod["usable_comments"] < 50].sort_values("usable_comments")
    extra = config.TEXT_EXTRA_STOP_WORDS
    lines = [
        "# Text corpus (SCRUM-23)",
        "",
        "Generated by `python3 src/build_text_corpus.py` from `data/processed/youtube_comments_clean.csv`; "
        "re-running it with the same inputs gives the same CSV files. Do not hand-edit.",
        "",
        "## Outputs",
        "",
        f"- `data/processed/{CORPUS_OUT}`: one row per unique comment of a product (**{n:,} rows**).",
        f"- `data/processed/{BY_PRODUCT_OUT}`: one row per registry product ({len(by_prod)} rows): comments, "
        "usable comments, tokens, top 10 terms.",
        f"- `data/processed/{REPORT_OUT}`: counts for every step below.",
        f"- `docs/figures/{TOP_TERMS_FIG}` and `docs/figures/{TOKENS_FIG}`.",
        "",
        "## Method",
        "",
        "1. **Extraction.** Only rows from each product's active query version are used (as in `clean_youtube.py`). "
        "Rows with empty text are dropped; nothing else is dropped. A comment seen in several snapshots is kept once, "
        "from its earliest snapshot (key `(product_id, comment_id)`). One video can be in several products' search "
        "results, so the same comment can belong to several products: it is kept once **per product**, and "
        "`n_products_sharing` says how many share it. Deduplicating on `comment_id` alone would remove one product "
        "entirely. `text_raw` is the cleaned table's text, unchanged.",
        "2. **`text_sentiment_ready`** (for sentiment): HTML leftovers removed, lowercased, URLs and @mentions removed "
        "(an `@` followed by a letter; `@7:28` timestamps stay), characters repeated 3+ times cut to 2 (`sooo` -> "
        "`soo`, `!!!!` -> `!!`; digits are never collapsed), whitespace collapsed. Stop-words, negations "
        "(`not`, `no`, `never`), punctuation and emojis are **kept**.",
        "3. **`text_clean`** (for topic modelling): from `text_sentiment_ready`, apostrophes joined (`don't` -> `dont`), "
        "video timestamps removed, then tokenized with the regex `(?:[^\\W_]|[\\u0900-\\u0963\\u0966-\\u0DFF])+` "
        "(runs of letters and digits, Latin and Indic scripts). Emojis and punctuation therefore fall away. "
        "Digits-only tokens and stop-words are removed, **except registry brand and model tokens**, which are always "
        "kept (for example `nothing`, `find`, `15`, `s25`, `pro`, `ultra`). `tokens` holds the same words as "
        "`text_clean` (space-joined) and `n_tokens` their count.",
        "4. **Flags (rows kept).** `is_emoji_only` and `likely_non_english` come from `clean_youtube.py`; "
        f"`is_empty_after_clean` (no token left) and `is_too_short` (fewer than {config.TEXT_MIN_TOKENS} tokens, "
        "empty included) are new. `is_usable` is true when none of the four is set. The model step filters on these.",
        "",
        "Stop-words (`config.TEXT_EXTRA_STOP_WORDS`): sklearn's `ENGLISH_STOP_WORDS` (318 words, no download) plus",
        f"- Hinglish ({len(extra['hinglish'])}): {', '.join(extra['hinglish'])}",
        f"- contraction leftovers and chat shorthand ({len(extra['contractions'])}): {', '.join(extra['contractions'])}",
        "",
        "## Counts",
        "",
        "| | value |",
        "|---|--:|",
        f"| rows in (cleaned comments table) | {steps['load']['rows_before']:,} |",
        f"| excluded: not the active query version | {steps['select_active_query']['rows_before'] - steps['select_active_query']['rows_after']:,} |",
        f"| dropped: empty text | {steps['drop_empty_text']['rows_before'] - steps['drop_empty_text']['rows_after']:,} |",
        f"| removed: same comment in a later snapshot | {steps['drop_duplicate_comments']['rows_before'] - steps['drop_duplicate_comments']['rows_after']:,} |",
        f"| **rows out** (unique comments per product) | **{n:,}** |",
        f"| unique comment ids | {corpus.loc[corpus['comment_id'] != '', 'comment_id'].nunique():,} |",
        f"| comments shared by 2+ products | {int((corpus['n_products_sharing'] > 1).sum()):,} rows |",
        f"| products covered | {corpus['product_id'].nunique()} |",
        f"| comments with URLs removed | {steps['remove_urls']['rows_flagged']:,} |",
        f"| comments with @mentions removed | {steps['remove_mentions']['rows_flagged']:,} |",
        f"| tokens per comment, mean / median | {corpus['n_tokens'].mean():.1f} / {corpus['n_tokens'].median():.0f} |",
        f"| flagged `is_empty_after_clean` | {share('is_empty_after_clean')} |",
        f"| flagged `is_too_short` | {share('is_too_short')} |",
        f"| flagged `is_emoji_only` | {share('is_emoji_only')} |",
        f"| flagged `likely_non_english` | {share('likely_non_english')} |",
        f"| **usable** (no flag) | **{share('is_usable')}** |",
        "",
        "Top 15 terms (usable comments, a shared comment counted once): "
        + ", ".join(f"{t} ({c:,})" for t, c in top15) + ".",
        "",
        "Products with fewer than 50 usable comments: "
        + (", ".join(f"{r.product_id} {r.product_name} ({r.usable_comments})" for r in thin.itertuples())
           or "none") + ".",
        "",
        "## Read before using",
        "",
        "- Comments are top-level only, from each product's 5 most-viewed sampled videos, in YouTube's relevance "
        "order, at most 100 per video. They are a narrow sample, not all comments about a phone.",
        "- `likely_non_english` judges the script, not the language: Hinglish and other romanised Indian languages "
        "count as English, so an English-only sentiment model will misread many usable comments. The Hinglish "
        "stop-word list only helps topic modelling.",
        "- Many comments are generic (\"nice video\", \"first\"); topic models may need a domain stop-list on top.",
        "",
        "## ⚠️ Leakage warning for SCRUM-24",
        "",
        "**The comments were collected on 2026-10-04** (the 10 pilot products also on 2026-10-03), as a "
        "relevance-ranked sample of the videos that were most viewed **by then**. Any per-day text feature built "
        "from `published_at` (comment counts, sentiment, topic shares per day) therefore reflects what had become "
        "popular by October, not what was visible on that day: a past day only has comments that survived into the "
        "October sample. **Text features must not be used as time-series predictors for earlier dates** without "
        "stating this caveat; treat them as product-level descriptors, or rebuild them from snapshots collected "
        "on (or before) each date.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None, paths=config.PATHS):
    parser = argparse.ArgumentParser(description="Build the YouTube comment text corpus (SCRUM-23).")
    parser.add_argument("--no-plots", action="store_true", help="skip the two figures")
    args = parser.parse_args(argv)

    in_path = paths.processed_dir / COMMENTS_IN
    if not in_path.exists() or in_path.stat().st_size == 0:
        print(f"Missing input {in_path}. Run src/clean_youtube.py first.", file=sys.stderr)
        return 1
    registry = read_products(paths.active_registry_csv)
    comments = pd.read_csv(in_path, dtype=str, keep_default_na=False, encoding="utf-8")
    corpus, report = build_corpus(comments, active_query_versions(paths), protected_terms(registry))
    by_prod = by_product(corpus, registry)
    top = top_terms_overall(corpus, max(TOP_TERMS_FIGURE, 15))

    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    corpus.to_csv(paths.processed_dir / CORPUS_OUT, index=False, lineterminator="\n")
    by_prod.to_csv(paths.processed_dir / BY_PRODUCT_OUT, index=False, lineterminator="\n")
    pd.DataFrame(report, columns=REPORT_HEADER).to_csv(paths.processed_dir / REPORT_OUT, index=False,
                                                       lineterminator="\n")
    docs = paths.root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / SUMMARY_DOC).write_text(summary_markdown(corpus, by_prod, report, top[:15]), encoding="utf-8")
    if not args.no_plots and len(corpus):
        (docs / "figures").mkdir(exist_ok=True)
        plot_top_terms(top[:TOP_TERMS_FIGURE], docs / "figures" / TOP_TERMS_FIG)
        plot_tokens_per_comment(corpus["n_tokens"], docs / "figures" / TOKENS_FIG)

    for r in report:
        flagged = f" flagged={r['rows_flagged']}" if r["rows_flagged"] != "" else ""
        print(f"  {r['step']:<32} {r['rows_before']:>6} -> {r['rows_after']:<6}{flagged}  {r['detail']}")
    print(f"{CORPUS_OUT}: {len(corpus)} rows, {corpus['product_id'].nunique()} products; "
          f"summary: docs/{SUMMARY_DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
