"""Descriptive text mining of the YouTube comment corpus: sentiment and topics (SCRUM-28).

This is the descriptive layer for the review and the dashboard: product-, cohort- and brand-level
summaries of what the comments say. It is not a feature builder. The leak-free daily model inputs
are SCRUM-24's processed/method2_* files (src/isolate_method2_data.py).

Inputs (read, never modified):
  data/processed/text_corpus.csv     src/build_text_corpus.py (SCRUM-23)
  data/processed/full_report.csv     src/full_report.py (Trends mean and last-7-day interest); optional
  active registry                    product names, brand, product_type, brand and model tokens
Outputs:
  data/processed/text_sentiment_comments.csv     one row per corpus row: sentiment, aspects, topics
  data/processed/text_sentiment_by_product.csv   one row per registry product
  data/processed/text_topics_terms.csv           top terms per topic (chosen LDA and NMF comparison)
  data/processed/text_topics_by_product.csv      topic and aspect shares per product
  data/processed/text_topic_model_selection.csv  LDA and NMF for every k, with the selection
  data/processed/text_mining_by_group.csv        sentiment, topic and aspect shares per product_type, brand
  data/processed/text_signal_link.csv            product-level correlations with Trends interest
  data/processed/text_sentiment_spotcheck.csv    100 scored comments with an empty human_label column
  docs/text_mining.md                            method, counts, findings, limitations (generated)
  docs/figures/text_*.png                        charts (see FIGURES)

Sentiment: VADER (vaderSentiment) compound on text_sentiment_ready, which keeps negations,
punctuation and emojis. Only usable comments (is_usable, none of the SCRUM-23 flags) are scored;
flagged rows stay in the output with the score missing and sentiment_skip_reason naming the flags.
Labels use config.TEXT_SENTIMENT_THRESHOLD. vader_no_sentiment_words marks compound == 0 exactly:
VADER found no word in its lexicon (or the words cancelled out), which is common for Hinglish.

Topics: one document per unique usable comment (a comment shared by several products is modelled
once), from the tokens column minus config.TEXT_TOPIC_STOP_WORDS, the registry brand and model
tokens, tokens containing a digit and one-character tokens. LDA on counts and NMF on TF-IDF are
fitted for every k in config.TEXT_TOPIC_K_RANGE (random_state fixed). Each fit is scored on
- coherence: mean NPMI of each topic's top 10 terms, from document co-occurrence in this corpus;
- stability: refitted on two 80% subsamples, the mean best-match Jaccard overlap of top-10 terms;
- overlap: mean pairwise Jaccard of top-10 terms between topics (lower is more distinct).
For each method k is the one with the best average rank of coherence and stability (ties: the
smaller k, which is easier to read). The reported topics are LDA's (shares sum to 1 per comment);
NMF is the comparison. A comment with no term left in the vocabulary has no topic (left missing).
Topic labels are suggestions from keyword themes (LABEL_THEMES) and must be confirmed by reading.

Aspects: SCRUM-24's DOMAIN_TOPIC_KEYWORDS (imported) minus config.TEXT_ASPECT_EXCLUDED_KEYWORDS. A
comment mentions an aspect if one of its tokens is in the list; aspect sentiment is the mean
compound of the scored comments that mention it (the whole comment's score, not aspect-level).

Groups (product_type, brand) count a comment shared by several products of the group once.
Every CSV depends only on the inputs, so re-running gives identical files. The spot-check file is
not overwritten once any human_label is filled in.

Usage: python src/text_mining.py [--no-plots] [--ks 4,5,6]
"""

import argparse
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.decomposition import NMF, LatentDirichletAllocation
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer

import config
from build_text_corpus import BLUE, CORPUS_OUT, GRID, INK, INK_2, SURFACE, _style, is_true, protected_terms
from common import read_products
from isolate_method2_data import DOMAIN_TOPIC_KEYWORDS

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except ImportError:  # main() reports it; SCRUM-28 needs real VADER, not SCRUM-24's fallback lexicon
    SentimentIntensityAnalyzer = None

FULL_REPORT_IN = "full_report.csv"
COMMENTS_OUT = "text_sentiment_comments.csv"
SENTIMENT_BY_PRODUCT_OUT = "text_sentiment_by_product.csv"
TOPIC_TERMS_OUT = "text_topics_terms.csv"
TOPICS_BY_PRODUCT_OUT = "text_topics_by_product.csv"
MODEL_SELECTION_OUT = "text_topic_model_selection.csv"
BY_GROUP_OUT = "text_mining_by_group.csv"
SIGNAL_LINK_OUT = "text_signal_link.csv"
SPOTCHECK_OUT = "text_sentiment_spotcheck.csv"
SUMMARY_DOC = "text_mining.md"
SPOTCHECK_LABEL_COLUMNS = ("human_label", "ai_label", "ai_note", "label_source")  # any filled: never overwrite
# src/evaluate_spotcheck.py writes its section between these markers; a rerun here keeps it.
EVAL_BLOCK_START, EVAL_BLOCK_END = "<!-- spotcheck-eval:start -->", "<!-- spotcheck-eval:end -->"
FIGURES = {
    "distribution": "text_sentiment_distribution.png",
    "by_type": "text_sentiment_by_product_type.png",
    "by_brand": "text_sentiment_by_brand.png",
    "top_bottom": "text_sentiment_top_bottom_products.png",
    "topic_terms": "text_topic_terms.png",
    "topic_by_type": "text_topic_share_by_product_type.png",
    "vs_interest": "text_sentiment_vs_interest.png",
}

FLAG_REASONS = (("is_emoji_only", "emoji_only"), ("likely_non_english", "likely_non_english"),
                ("is_empty_after_clean", "empty_after_clean"), ("is_too_short", "too_short"))
LABELS = ("positive", "neutral", "negative")
PRODUCT_FIELDS = ["product_id", "product_name", "brand", "product_type"]
PRODUCT_TYPES = ("RISING", "STABLE", "DECLINING")
ASPECTS = {name.removeprefix("topic_"): frozenset(words) - set(config.TEXT_ASPECT_EXCLUDED_KEYWORDS)
           for name, words in DOMAIN_TOPIC_KEYWORDS.items()}
TOPIC_STOP_WORDS = frozenset().union(*config.TEXT_TOPIC_STOP_WORDS.values())
TOP_N_TERMS = 10
LDA_MAX_ITER = 20
STABILITY_SUBSAMPLES = 2
STABILITY_FRACTION = 0.8
DECIMALS = 4

# Keyword themes for the suggested topic labels: the theme with most of a topic's top 10 terms (2+) names
# it, and a second theme is added when it also has 3+. Earlier themes win ties, so a language
# cluster is named as such even when it also mentions specs.
LABEL_THEMES = {
    # likely_non_english only catches non-Latin scripts; Indonesian comments pass it
    "Indonesian & other languages": {"hp", "di", "yg", "yang", "ini", "nya", "ada", "aja", "dan", "harga", "gak",
                                     "ga", "udah", "bang", "mas", "banget", "kalo", "sama"},
    "Camera": ASPECTS["camera"],
    "Battery & charging": ASPECTS["battery"],
    "Performance & gaming": ASPECTS["performance"],
    "Price & value": ASPECTS["price_value"],
    "Design & display": ASPECTS["design_display"] | {"color", "colour", "size", "big", "small", "feel", "feels"},
    "Specs & hardware": ASPECTS["camera"] | ASPECTS["battery"] | ASPECTS["performance"]
    | {"features", "feature", "gen", "ram", "storage", "specs", "chip", "quality", "life", "long"},
    "Buying advice": {"buy", "lena", "chahiye", "kon", "konsa", "suggest", "purchase", "recommend", "budget"},
    "Upgrading & ownership": {"upgrade", "upgraded", "upgrading", "years", "year", "old", "using", "used",
                              "bought", "switch", "switched", "owner"},
    "Software, AI & updates": {"ai", "software", "update", "updates", "android", "ios", "feature", "features",
                               "app", "apps", "os"},
    "Comparisons": {"vs", "comparison", "compare", "compared", "difference", "better"},
    "Launch & waiting": {"launch", "launched", "waiting", "wait", "kab", "finally", "release", "coming", "aayega"},
    "Praise & reactions": {"love", "best", "great", "amazing", "awesome", "nice", "wow", "super", "cool", "good",
                           "beautiful", "lol", "crazy", "insane"},
}
LABEL_STATUS = "suggestion: confirm by reading the terms"

POSITIVE, NEUTRAL, NEGATIVE = BLUE, "#c3c2b7", "#e34948"
TYPE_COLORS = {"RISING": "#2a78d6", "STABLE": "#eb6834", "DECLINING": "#1baf7a"}
SEQUENTIAL = ("#f0f5fc", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b")


# --- sentiment ------------------------------------------------------------------------------------
def skip_reasons(corpus):
    parts = [np.where(is_true(corpus[col]), name, "") for col, name in FLAG_REASONS]
    return pd.Series([";".join(p for p in row if p) for row in zip(*parts)], index=corpus.index, dtype=str)


def sentiment_label(compound, threshold=config.TEXT_SENTIMENT_THRESHOLD):
    if pd.isna(compound):
        return ""
    if compound >= threshold:
        return "positive"
    return "negative" if compound <= -threshold else "neutral"


def score_sentiment(corpus, analyzer=None):
    """VADER compound and label for usable rows; flagged rows keep a missing score and a reason."""
    if analyzer is None:
        if SentimentIntensityAnalyzer is None:
            raise ImportError("vaderSentiment is required for score_sentiment. Install vaderSentiment.")
        analyzer = SentimentIntensityAnalyzer()
    usable = is_true(corpus["is_usable"])
    cache = {}
    compound = pd.Series(np.nan, index=corpus.index)
    for i, text in corpus.loc[usable, "text_sentiment_ready"].items():
        if text not in cache:
            cache[text] = analyzer.polarity_scores(text)["compound"]
        compound[i] = cache[text]
    out = pd.DataFrame({"sentiment_skip_reason": skip_reasons(corpus).where(~usable, ""),
                        "sentiment_compound": compound}, index=corpus.index)
    out["sentiment_label"] = compound.map(sentiment_label)
    out["vader_no_sentiment_words"] = compound.map(lambda c: "" if pd.isna(c) else str(c == 0))
    return out


def aspect_mentions(tokens):
    sets = tokens.fillna("").str.split().map(set)
    return pd.DataFrame({f"aspect_{name}": sets.map(lambda s, w=words: bool(s & w)) for name, words in
                         ASPECTS.items()}, index=tokens.index)


# --- topics ---------------------------------------------------------------------------------------
def doc_key(corpus):
    """A comment's identity across products: comment_id, or its cleaned text when the id is missing."""
    return corpus["comment_id"].where(corpus["comment_id"] != "", "text:" + corpus["text_clean"])


def topic_documents(corpus, protected):
    """One document per unique usable comment: tokens minus the domain stop-list and brand/model tokens."""
    drop = TOPIC_STOP_WORDS | set(protected)

    def keep(word):
        return len(word) >= 2 and word not in drop and not any(ch.isdigit() for ch in word)

    usable = corpus[is_true(corpus["is_usable"])]
    docs = usable.assign(key=doc_key(usable)).drop_duplicates("key").set_index("key")["tokens"]
    return docs.map(lambda t: " ".join(w for w in t.split() if keep(w)))


def brand_kept_documents(corpus):
    """The same documents with brand and model tokens kept (for the comparison row only)."""
    usable = corpus[is_true(corpus["is_usable"])]
    docs = usable.assign(key=doc_key(usable)).drop_duplicates("key").set_index("key")["tokens"]
    return docs.map(lambda t: " ".join(w for w in t.split() if len(w) >= 2 and w not in TOPIC_STOP_WORDS))


def vectorize(docs, method, min_df=1, max_df=1.0, vocabulary=None):
    cls = CountVectorizer if method == "lda" else TfidfVectorizer
    vec = cls(token_pattern=r"\S+", lowercase=False, min_df=min_df, max_df=max_df, vocabulary=vocabulary)
    return vec, vec.fit_transform(docs)


def fit_topic_model(method, X, k, random_state=config.TEXT_TOPIC_RANDOM_STATE):
    if method == "lda":
        model = LatentDirichletAllocation(n_components=k, random_state=random_state, max_iter=LDA_MAX_ITER,
                                          learning_method="batch")
    else:
        model = NMF(n_components=k, random_state=random_state, init="nndsvda", max_iter=500)
    return model, model.fit_transform(X)


def top_term_indices(model, n=TOP_N_TERMS):
    return [list(np.argsort(-comp, kind="stable")[:n]) for comp in model.components_]


def npmi_coherence(top_indices, X):
    """Mean NPMI over the term pairs of each topic, from document co-occurrence (binary X)."""
    B = (X > 0).astype(np.float64).tocsc()
    n_docs = B.shape[0]
    scores = []
    for top in top_indices:
        sub = B[:, top]
        co = (sub.T @ sub).toarray() / n_docs
        p = np.diag(co)
        pairs = []
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                pij = co[i, j]
                if pij == 0:
                    pairs.append(-1.0)
                elif pij >= 1:
                    pairs.append(1.0)
                else:
                    pairs.append(np.log(pij / (p[i] * p[j])) / -np.log(pij))
        scores.append(float(np.mean(pairs)) if pairs else 0.0)
    return scores


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def topic_overlap(term_lists):
    pairs = [jaccard(a, b) for i, a in enumerate(term_lists) for b in term_lists[i + 1:]]
    return float(np.mean(pairs)) if pairs else 0.0


def stability(method, docs, k, base_terms, min_df, max_df, random_state=config.TEXT_TOPIC_RANDOM_STATE):
    """Mean best-match Jaccard of the top terms against fits on 80% subsamples (fixed seeds)."""
    scores = []
    for s in range(1, STABILITY_SUBSAMPLES + 1):
        rng = np.random.default_rng(random_state + s)
        size = min(len(docs), max(k, int(len(docs) * STABILITY_FRACTION)))
        idx = np.sort(rng.choice(len(docs), size=size, replace=False))
        vec, X = vectorize(docs.iloc[idx], method, min_df, max_df)
        if k > min(X.shape):
            continue
        model, _ = fit_topic_model(method, X, k, random_state)
        vocab = vec.get_feature_names_out()
        sub_terms = [[vocab[i] for i in top] for top in top_term_indices(model)]
        scores.append(np.mean([max(jaccard(t, s_) for s_ in sub_terms) for t in base_terms]))
    return float(np.mean(scores)) if scores else np.nan


def brand_term_share(term_lists, protected):
    terms = [t for terms in term_lists for t in terms]
    hits = [t for t in terms if t in protected or any(ch.isdigit() for ch in t)]
    return len(hits) / len(terms) if terms else 0.0


def suggest_label(terms):
    hits = sorted(((sum(t in words for t in terms), -i, theme) for i, (theme, words) in
                   enumerate(LABEL_THEMES.items())), reverse=True)
    if hits[0][0] < 2:
        return "Mixed: " + ", ".join(terms[:3])
    return hits[0][2] + (f" / {hits[1][2]}" if hits[1][0] >= 3 else "")


def select_k(rows):
    """Best average rank of coherence and stability per method; ties go to the smaller k."""
    df = pd.DataFrame(rows)
    df["rank_coherence"] = df.groupby("method")["coherence_npmi"].rank(ascending=False, method="min")
    df["rank_stability"] = df.groupby("method")["stability"].rank(ascending=False, method="min")
    df["mean_rank"] = (df["rank_coherence"] + df["rank_stability"]) / 2
    best = df.sort_values(["method", "mean_rank", "k"]).groupby("method").head(1)
    df["selected_for_method"] = df.index.isin(best.index)
    return df


def run_topic_models(corpus, protected, ks, min_df, max_df, random_state=config.TEXT_TOPIC_RANDOM_STATE):
    """Fit LDA and NMF for every k, choose k per method, and return everything the outputs need."""
    docs = topic_documents(corpus, protected)
    # One vocabulary for both methods (min_df/max_df on counts); comments left with no vocabulary term
    # are not modelled, so they get a missing topic rather than LDA's uniform guess.
    vocab_set = set(CountVectorizer(token_pattern=r"\S+", lowercase=False, min_df=min_df, max_df=max_df)
                    .fit(docs[docs != ""]).vocabulary_)
    filtered = docs.map(lambda t: " ".join(w for w in t.split() if w in vocab_set))
    modelled = filtered[filtered != ""]
    fits, rows = {}, []
    for method in ("lda", "nmf"):
        vec, X = vectorize(modelled, method, vocabulary=sorted(vocab_set))
        vocab = vec.get_feature_names_out()
        for k in ks:
            if k > min(X.shape):
                continue
            model, W = fit_topic_model(method, X, k, random_state)
            tops = top_term_indices(model)
            terms = [[vocab[i] for i in top] for top in tops]
            fits[(method, k)] = (model, W, terms, vocab)
            rows.append({"method": method, "vocabulary": "domain_stop_list_no_brand_terms", "k": k,
                         "documents": X.shape[0], "vocabulary_size": X.shape[1],
                         "coherence_npmi": float(np.mean(npmi_coherence(tops, X))),
                         "stability": stability(method, modelled, k, terms, min_df, max_df, random_state),
                         "topic_overlap": topic_overlap(terms),
                         "brand_term_share": brand_term_share(terms, protected)})
    if not rows:
        raise ValueError("no topic model could be fitted: too few documents or terms")
    selection = select_k(rows)
    chosen = {m: int(selection.loc[selection["selected_for_method"] & (selection["method"] == m), "k"].iloc[0])
              for m in selection["method"].unique()}

    # Evidence for the brand-word decision: the chosen k again, with brand and model tokens kept.
    extra = []
    branded = brand_kept_documents(corpus).loc[modelled.index]
    for method, k in chosen.items():
        vec, X = vectorize(branded, method, min_df, max_df)
        if k > min(X.shape):
            continue
        model, _ = fit_topic_model(method, X, k, random_state)
        vocab = vec.get_feature_names_out()
        tops = top_term_indices(model)
        terms = [[vocab[i] for i in top] for top in tops]
        extra.append({"method": method, "vocabulary": "brand_and_model_terms_kept", "k": k,
                      "documents": X.shape[0], "vocabulary_size": X.shape[1],
                      "coherence_npmi": float(np.mean(npmi_coherence(tops, X))), "stability": np.nan,
                      "topic_overlap": topic_overlap(terms), "brand_term_share": brand_term_share(terms, protected),
                      "selected_for_method": False, "top_terms": " | ".join(" ".join(t[:5]) for t in terms)})
    selection["top_terms"] = [" | ".join(" ".join(t[:5]) for t in fits[(r.method, r.k)][2])
                              for r in selection.itertuples()]
    if extra:
        selection = pd.concat([selection, pd.DataFrame(extra)], ignore_index=True)
    return {"docs": docs, "modelled": modelled, "fits": fits, "chosen": chosen, "selection": selection}


def topic_terms_table(topics):
    rows = []
    for method, k in sorted(topics["chosen"].items()):
        model, W, terms, vocab = topics["fits"][(method, k)]
        weights = model.components_ / model.components_.sum(axis=1, keepdims=True)
        totals = W.sum(axis=1, keepdims=True)
        shares = W / np.where(totals == 0, 1, totals)
        dominant = np.bincount(shares[totals.ravel() > 0].argmax(axis=1), minlength=k)
        for t in range(k):
            label = suggest_label(terms[t])
            for rank, idx in enumerate(top_term_indices(model)[t], start=1):
                rows.append({"method": method, "k": k, "topic": t + 1, "label_suggestion": label,
                             "label_status": LABEL_STATUS, "rank": rank, "term": vocab[idx],
                             "weight": weights[t, idx], "topic_mean_share": shares[:, t].mean(),
                             "dominant_comments": int(dominant[t])})
    return pd.DataFrame(rows)


def comment_topic_shares(topics, keys):
    """LDA topic shares per corpus row (via its comment key); rows without a topic stay missing."""
    k = topics["chosen"]["lda"]
    _, W, _, _ = topics["fits"][("lda", k)]
    cols = [f"topic_{t}" for t in range(1, k + 1)]
    shares = pd.DataFrame(W, index=topics["modelled"].index, columns=cols)
    out = shares.reindex(keys.values)
    out.index = keys.index
    has_topic = out[cols].notna().all(axis=1)
    dominant = out[cols].fillna(-1).to_numpy().argmax(axis=1) + 1
    out.insert(0, "dominant_topic", pd.Series(dominant.astype(str), index=out.index).where(has_topic, ""))
    out.insert(1, "dominant_topic_share", out[cols].max(axis=1))
    return out


# --- aggregates -----------------------------------------------------------------------------------
def sentiment_stats(frame):
    scored = frame[frame["sentiment_compound"].notna()]
    n = len(scored)
    weights = 1 + pd.to_numeric(scored["like_count"], errors="coerce").fillna(0)
    out: dict = {"comments": len(frame), "scored_comments": n, "unscored_comments": len(frame) - n,
           "mean_compound": scored["sentiment_compound"].mean() if n else np.nan,
           "median_compound": scored["sentiment_compound"].median() if n else np.nan}
    for label in LABELS:
        out[f"share_{label}"] = (scored["sentiment_label"] == label).mean() if n else np.nan
    out["share_no_sentiment_words"] = (scored["vader_no_sentiment_words"] == "True").mean() if n else np.nan
    out["like_weighted_mean_compound"] = np.average(scored["sentiment_compound"], weights=weights) if n else np.nan
    return out


def topic_aspect_stats(frame, topic_cols):
    usable = frame[frame["sentiment_compound"].notna()]
    with_topic = usable[usable["dominant_topic"] != ""]
    out: dict = {"usable_comments": len(usable), "comments_with_topic": len(with_topic)}
    means = with_topic[topic_cols].mean() if len(with_topic) else pd.Series(np.nan, index=topic_cols)
    for col in topic_cols:
        out[f"{col}_share"] = means[col]
    out["dominant_topic"] = str(means.idxmax()).removeprefix("topic_") if len(with_topic) else ""
    for name in ASPECTS:
        mask = usable[f"aspect_{name}"] == "True"
        out[f"aspect_{name}_comments"] = int(mask.sum())
        out[f"aspect_{name}_share"] = mask.mean() if len(usable) else np.nan
        out[f"aspect_{name}_mean_compound"] = usable.loc[mask, "sentiment_compound"].mean() if mask.any() else np.nan
    return out


def per_product(comments, registry, stats_fn):
    rows = []
    for p in registry:
        row = {f: p[f] for f in PRODUCT_FIELDS}
        row.update(stats_fn(comments[comments["product_id"] == p["product_id"]]))
        rows.append(row)
    return pd.DataFrame(rows)


def by_group(comments, registry, topic_cols):
    """product_type and brand rows; a comment shared by products of one group counts once."""
    fields = pd.DataFrame(registry)[PRODUCT_FIELDS]
    merged = comments.drop(columns=[c for c in ("brand", "product_type") if c in comments]).merge(
        fields[["product_id", "brand", "product_type"]], on="product_id", how="left")
    rows = []
    for group_type, order in (("product_type", PRODUCT_TYPES), ("brand", sorted(fields["brand"].unique()))):
        for group in order:
            frame = merged[merged[group_type] == group].drop_duplicates("comment_key")
            row = {"group_type": group_type, "group": group,
                   "products": int((fields[group_type] == group).sum())}
            row.update(sentiment_stats(frame))
            row.update(topic_aspect_stats(frame, topic_cols))
            rows.append(row)
    return pd.DataFrame(rows)


def strength(rho):
    if pd.isna(rho):
        return ""
    a = abs(rho)
    return "negligible" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong"


def signal_link(sent_by_product, full_report):
    """Product-level Pearson and Spearman correlations; descriptive, not causal."""
    trends = full_report[["product_id", "mean_interest", "last_7d_mean"]].copy()
    for col in ("mean_interest", "last_7d_mean"):
        trends[col] = pd.to_numeric(trends[col], errors="coerce")
    merged = sent_by_product.merge(trends, on="product_id", how="inner")
    rows = []
    for text_col in ("mean_compound", "like_weighted_mean_compound", "comments"):
        for trend_col in ("mean_interest", "last_7d_mean"):
            pair = merged[[text_col, trend_col]].apply(pd.to_numeric, errors="coerce").dropna()
            n = len(pair)
            row = {"text_measure": text_col, "trends_measure": trend_col, "n_products": n,
                   "pearson_r": np.nan, "pearson_p": np.nan, "spearman_rho": np.nan, "spearman_p": np.nan}
            if n >= 3 and pair[text_col].nunique() > 1 and pair[trend_col].nunique() > 1:
                row["pearson_r"], row["pearson_p"] = stats.pearsonr(pair[text_col], pair[trend_col])
                row["spearman_rho"], row["spearman_p"] = stats.spearmanr(pair[text_col], pair[trend_col])
            row["strength_spearman"] = strength(row["spearman_rho"])
            rows.append(row)
    return pd.DataFrame(rows), merged


def spotcheck(comments, corpus, n=config.TEXT_SPOTCHECK_SIZE, seed=config.TEXT_TOPIC_RANDOM_STATE):
    scored = comments[comments["sentiment_compound"].notna()].drop_duplicates("comment_key")
    sample = scored.sample(n=min(n, len(scored)), random_state=seed)
    return pd.DataFrame({"product_id": sample["product_id"], "comment_id": sample["comment_id"],
                         "text": corpus.loc[sample.index, "text_raw"], "vader_label": sample["sentiment_label"],
                         "human_label": ""})


def spotcheck_has_labels(path):
    if not path.exists():
        return False
    existing = pd.read_csv(path, dtype=str, keep_default_na=False)
    return any(col in existing and (existing[col].str.strip() != "").any() for col in SPOTCHECK_LABEL_COLUMNS)


def eval_block(path):
    """The spot-check evaluation section of an existing summary doc, if any."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    start, end = text.find(EVAL_BLOCK_START), text.find(EVAL_BLOCK_END)
    return text[start:end + len(EVAL_BLOCK_END)] + "\n" if 0 <= start < end else ""


def round_floats(df):
    return df.apply(lambda s: s.round(DECIMALS) if s.dtype.kind == "f" else s)


def build_comments(corpus, topics):
    sentiment = score_sentiment(corpus)
    keys = doc_key(corpus)
    usable = is_true(corpus["is_usable"])
    aspects = aspect_mentions(corpus["tokens"]).astype(str).where(usable, "")
    out = corpus[["product_id", "comment_id", "video_id", "published_at", "like_count", "n_products_sharing",
                  "is_usable"]].copy()
    out = pd.concat([out, sentiment, aspects, comment_topic_shares(topics, keys)], axis=1)
    out["dominant_topic"] = out["dominant_topic"].fillna("")
    out["comment_key"] = keys
    return out


# --- charts ---------------------------------------------------------------------------------------
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _title(ax, title, subtitle=None):
    lines = subtitle.count("\n") + 1 if subtitle else 0
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold", color=INK, pad=10 + 16 * lines)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=10, color=INK_2, va="bottom")


def _save(fig, path, rect=(0, 0, 1, 1)):
    fig.tight_layout(rect=rect)
    fig.savefig(path, dpi=150, metadata={"Software": None})
    _plt().close(fig)


def plot_distribution(comments, path):
    plt = _plt()
    scored = comments.drop_duplicates("comment_key")["sentiment_compound"].dropna()
    t = config.TEXT_SENTIMENT_THRESHOLD
    fig, ax = plt.subplots(figsize=(9, 5))
    _style(ax, fig)
    bins = np.linspace(-1, 1, 41)
    counts, edges = np.histogram(scored, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    colors = [NEGATIVE if c <= -t else POSITIVE if c >= t else NEUTRAL for c in centers]
    ax.bar(centers, counts, width=0.05, color=colors, edgecolor=SURFACE, linewidth=1.5)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    zero = int((scored == 0).sum())
    shares = {lab: (comments.drop_duplicates("comment_key")["sentiment_label"] == lab).sum() / len(scored)
              for lab in LABELS}
    _title(ax, "Sentiment of YouTube comments (VADER compound score)",
           f"{len(scored):,} scored comments: {shares['positive']:.0%} positive, {shares['neutral']:.0%} neutral, "
           f"{shares['negative']:.0%} negative.\nThe tall bar at 0: {zero:,} comments in which VADER found no "
           "sentiment word (often Hinglish or other languages)")
    ax.set_xlabel("Compound score (-1 most negative, +1 most positive); neutral band is -0.05 to 0.05",
                  fontsize=10, color=INK_2)
    ax.set_ylabel("Comments", fontsize=10, color=INK_2)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=lab) for lab, c in
                       (("negative", NEGATIVE), ("neutral", NEUTRAL), ("positive", POSITIVE))],
              frameon=False, loc="upper left", fontsize=10)
    _save(fig, path)


def plot_label_shares(groups, path, title, subtitle):
    plt = _plt()
    groups = groups[groups["scored_comments"] > 0].iloc[::-1]  # an empty group has no shares to draw
    fig, ax = plt.subplots(figsize=(9, 2.6 + 0.42 * len(groups)))
    _style(ax, fig)
    left = np.zeros(len(groups))
    for label, color in (("negative", NEGATIVE), ("neutral", NEUTRAL), ("positive", POSITIVE)):
        values = groups[f"share_{label}"].to_numpy() * 100
        ax.barh(groups["display"], values, left=left, height=0.62, color=color, edgecolor=SURFACE, linewidth=2,
                label=label)
        for y, (l, v) in enumerate(zip(left, values)):
            if v >= 5:
                ax.text(l + v / 2, y, f"{v:.0f}%", ha="center", va="center", fontsize=9,
                        color="white" if color != NEUTRAL else INK)
        left += values
    for y, m in enumerate(groups["mean_compound"]):
        ax.text(101.5, y, f"{m:+.2f}", va="center", fontsize=9, color=INK_2)
    ax.text(101.5, len(groups) - 0.6, "mean", fontsize=9, color=INK_2, va="bottom")
    ax.set_xlim(0, 109)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.tick_params(axis="y", labelsize=10, colors=INK)
    ax.set_xlabel("Share of scored comments (%)", fontsize=10, color=INK_2)
    _title(ax, title, subtitle)
    fig.legend(*ax.get_legend_handles_labels(), frameon=False, loc="lower center", ncol=3, fontsize=10)
    _save(fig, path, rect=(0, 0.4 / fig.get_figheight(), 1, 1))


def plot_top_bottom(sent, path, n=10):
    plt = _plt()
    ranked = sent.dropna(subset=["mean_compound"]).sort_values(["mean_compound", "product_id"], ascending=False)
    top, bottom = ranked.head(n), ranked.tail(n)
    lo = min(0, ranked["mean_compound"].min()) - 0.02
    hi = ranked["mean_compound"].max() * 1.15
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.6), sharex=True)
    for ax, part, name in ((axes[0], top, f"Most positive {n}"), (axes[1], bottom, f"Least positive {n}")):
        _style(ax, fig)
        part = part.iloc[::-1]
        labels = [f"{r.product_name} ({r.scored_comments})" for r in part.itertuples()]
        ax.barh(labels, part["mean_compound"], height=0.6, color=BLUE)
        for y, v in enumerate(part["mean_compound"]):
            ax.text(v + 0.005 if v >= 0 else v - 0.005, y, f"{v:.2f}", va="center",
                    ha="left" if v >= 0 else "right", fontsize=9, color=INK_2)
        ax.axvline(0, color=INK_2, linewidth=0.8)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_xlim(lo, hi)
        ax.tick_params(axis="y", labelsize=9.5, colors=INK)
        ax.set_title(name, loc="left", fontsize=11, color=INK)
        ax.set_xlabel("Mean VADER compound of scored comments", fontsize=10, color=INK_2)
    fig.suptitle("Products by mean comment sentiment (scored comments in brackets)", x=0.01, ha="left",
                 fontsize=13, fontweight="bold", color=INK)
    _save(fig, path)


def plot_topic_terms(terms, path):
    plt = _plt()
    lda = terms[terms["method"] == "lda"]
    k = lda["topic"].nunique()
    cols = 3 if k > 4 else 2
    rows = int(np.ceil(k / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.4 * cols, 3.3 * rows), squeeze=False)
    for i, ax in enumerate(axes.flat):
        if i >= k:
            ax.set_visible(False)
            continue
        _style(ax, fig)
        t = lda[lda["topic"] == i + 1].sort_values("rank", ascending=False)
        ax.barh(t["term"], t["weight"], height=0.62, color=BLUE)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.tick_params(axis="y", labelsize=9.5, colors=INK)
        ax.tick_params(axis="x", labelsize=8)
        share = t["topic_mean_share"].iloc[0]
        ax.set_title(f"Topic {i + 1}: {t['label_suggestion'].iloc[0]}\n{share:.0%} of comment text",
                     loc="left", fontsize=10, color=INK)
    fig.suptitle(f"LDA topics (k={k}): top 10 terms by weight; labels are suggestions to confirm",
                 x=0.01, ha="left", fontsize=13, fontweight="bold", color=INK)
    _save(fig, path)


def plot_topic_share_by_type(groups, terms, path):
    plt = _plt()
    from matplotlib.colors import LinearSegmentedColormap
    types = groups[groups["group_type"] == "product_type"].set_index("group").loc[list(PRODUCT_TYPES)]
    lda = terms[terms["method"] == "lda"].drop_duplicates("topic").sort_values("topic")
    cols = [f"topic_{t}_share" for t in lda["topic"]]
    data = types[cols].to_numpy(dtype=float).T * 100
    fig, ax = plt.subplots(figsize=(9, 2.4 + 0.5 * len(cols)))
    _style(ax, fig)
    cmap = LinearSegmentedColormap.from_list("blues", SEQUENTIAL)
    lo, hi = max(0.0, data.min() - 5), max(data.max(), 1)  # the scale starts near the smallest cell
    ax.imshow(data, cmap=cmap, aspect="auto", vmin=lo, vmax=hi)
    for (r, c), v in np.ndenumerate(data):
        ax.text(c, r, f"{v:.0f}%", ha="center", va="center", fontsize=10,
                color="white" if v > lo + (hi - lo) * 0.55 else INK)
    ax.set_xticks(range(len(PRODUCT_TYPES)),
                  [f"{t}\n({int(types.loc[t, 'comments_with_topic']):,} comments)" for t in PRODUCT_TYPES])
    ax.set_yticks(range(len(cols)), [f"{r.topic}: {r.label_suggestion}" for r in lda.itertuples()])
    ax.tick_params(colors=INK, labelsize=9.5, length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    _title(ax, "Topic share by product cohort",
           "Mean LDA topic share of comments in each cohort; each column sums to 100%.\n"
           f"Colour scale runs from {lo:.0f}% to {hi:.0f}% so differences show")
    _save(fig, path)


def plot_vs_interest(merged, link, path):
    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    specs = ((axes[0], "mean_compound", "Mean comment sentiment (VADER compound)"),
             (axes[1], "comments", "Comments in the corpus (mostly set by the sample design)"))
    for ax, col, ylabel in specs:
        _style(ax, fig)
        for ptype in PRODUCT_TYPES:
            part = merged[merged["product_type"] == ptype]
            ax.scatter(part["mean_interest"], part[col], s=46, color=TYPE_COLORS[ptype], edgecolor=SURFACE,
                       linewidth=1.5, label=ptype, zorder=3)
        ax.grid(color=GRID, linewidth=0.8)
        row = link[(link["text_measure"] == col) & (link["trends_measure"] == "mean_interest")].iloc[0]
        ax.set_title(f"Spearman rho {row['spearman_rho']:+.2f} (p={row['spearman_p']:.2f}, "
                     f"n={int(row['n_products'])}): {row['strength_spearman']}", loc="left", fontsize=10.5, color=INK)
        ax.set_xlabel("Mean Google Trends interest over the 269-day window (0-100)", fontsize=10, color=INK_2)
        ax.set_ylabel(ylabel, fontsize=10, color=INK_2)
    axes[0].legend(frameon=False, fontsize=9.5, loc="lower right")
    fig.suptitle("Comment sentiment and volume vs search interest, one dot per product (descriptive only)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold", color=INK)
    _save(fig, path)


def make_plots(comments, sent, groups, terms, link, merged, fig_dir):
    fig_dir.mkdir(parents=True, exist_ok=True)
    plot_distribution(comments, fig_dir / FIGURES["distribution"])
    types = groups[groups["group_type"] == "product_type"].assign(
        display=lambda d: d["group"] + " (" + d["scored_comments"].map("{:,}".format) + ")")
    plot_label_shares(types, fig_dir / FIGURES["by_type"], "Comment sentiment by product cohort",
                      "Share of scored comments per VADER label; scored comments in brackets")
    brands = groups[groups["group_type"] == "brand"].sort_values(["mean_compound", "group"], ascending=False)
    brands = brands.assign(display=lambda d: d["group"] + " (" + d["products"].map(
        lambda n: f"{n} product" + ("s" if n != 1 else "")) + ", " + d["scored_comments"].map("{:,}".format) + ")")
    plot_label_shares(brands, fig_dir / FIGURES["by_brand"], "Comment sentiment by brand",
                      "Sorted by mean compound; products and scored comments in brackets")
    plot_top_bottom(sent, fig_dir / FIGURES["top_bottom"])
    plot_topic_terms(terms, fig_dir / FIGURES["topic_terms"])
    plot_topic_share_by_type(groups, terms, fig_dir / FIGURES["topic_by_type"])
    if len(merged):
        plot_vs_interest(merged, link, fig_dir / FIGURES["vs_interest"])


# --- summary --------------------------------------------------------------------------------------
def _pct(x):
    return "" if pd.isna(x) else f"{x:.1%}"


def _num(x, fmt="{:.3f}"):
    return "" if pd.isna(x) else fmt.format(x)


def stop_word_frequencies(corpus):
    usable = corpus[is_true(corpus["is_usable"])]
    docs = usable.assign(key=doc_key(usable)).drop_duplicates("key")["tokens"].str.split().map(set)
    n = len(docs)
    return {group: [(w, sum(w in d for d in docs) / n if n else 0.0) for w in words]
            for group, words in config.TEXT_TOPIC_STOP_WORDS.items()}


def summary_markdown(corpus, comments, sent, groups, topics, terms, link, spot_note):
    unique = comments.drop_duplicates("comment_key")
    scored = unique[unique["sentiment_compound"].notna()]
    n_scored = len(scored)
    shares = {lab: (scored["sentiment_label"] == lab).mean() for lab in LABELS}
    zero = (scored["vader_no_sentiment_words"] == "True").sum()
    neutral = (scored["sentiment_label"] == "neutral").sum()
    reasons = comments.loc[comments["sentiment_skip_reason"] != "", "sentiment_skip_reason"].str.split(";").explode()
    sel = topics["selection"]
    k_lda, k_nmf = topics["chosen"].get("lda"), topics["chosen"].get("nmf")
    main_sel = sel[sel["vocabulary"] == "domain_stop_list_no_brand_terms"]
    branded = sel[sel["vocabulary"] == "brand_and_model_terms_kept"]
    lda_terms = terms[terms["method"] == "lda"]
    types = groups[groups["group_type"] == "product_type"].set_index("group")
    brands = groups[groups["group_type"] == "brand"].sort_values("mean_compound", ascending=False)
    ranked = sent.dropna(subset=["mean_compound"]).sort_values("mean_compound", ascending=False)
    shared = int((pd.to_numeric(corpus["n_products_sharing"], errors="coerce") > 1).sum())
    indonesian = scored.index.isin(corpus.index[corpus["tokens"].str.split().map(
        lambda ws: sum(w in LABEL_THEMES["Indonesian & other languages"] for w in set(ws)) >= 2)])
    zero_share = zero / n_scored if n_scored else np.nan

    lines = [
        "# Text mining: sentiment and topics (SCRUM-28)", "",
        f"Generated by `python3 src/text_mining.py` from `data/processed/{CORPUS_OUT}` (SCRUM-23). Do not "
        "hand-edit; re-running with the same inputs gives the same CSV files.", "",
        "**Descriptive only.** These outputs describe what the comments say per product, cohort and brand, "
        "for the review and the dashboard. They are not model features: the leak-free daily inputs for "
        "modelling are SCRUM-24's `processed/method2_*` files (`src/isolate_method2_data.py`).", "",
        "## Outputs", "",
        f"- `data/processed/{COMMENTS_OUT}`: one row per corpus row ({len(comments):,}): VADER score and label "
        "(missing for flagged rows, with `sentiment_skip_reason`), `vader_no_sentiment_words`, aspect mentions, "
        "LDA topic shares and dominant topic. Join to the text on `(product_id, comment_id)`.",
        f"- `data/processed/{SENTIMENT_BY_PRODUCT_OUT}`: sentiment per registry product ({len(sent)} rows).",
        f"- `data/processed/{TOPIC_TERMS_OUT}`: top 10 terms per topic for the chosen LDA (k={k_lda}) and the "
        f"NMF comparison (k={k_nmf}), with suggested labels.",
        f"- `data/processed/{TOPICS_BY_PRODUCT_OUT}`: topic and aspect shares, aspect sentiment, per product.",
        f"- `data/processed/{MODEL_SELECTION_OUT}`: every LDA and NMF fit with coherence, stability, overlap.",
        f"- `data/processed/{BY_GROUP_OUT}`: sentiment, topic and aspect shares per product_type and brand.",
        f"- `data/processed/{SIGNAL_LINK_OUT}`: product-level correlations with Google Trends interest.",
        f"- `data/processed/{SPOTCHECK_OUT}`: {spot_note}",
        "- Charts in `docs/figures/`: " + ", ".join(f"`{f}`" for f in FIGURES.values()) + ".",
        "- Notebook: `notebooks/05_text_mining.ipynb`.", "",
        "## Sentiment", "",
        "- Tool: VADER (`vaderSentiment`) compound score on `text_sentiment_ready`, which keeps negations, "
        "punctuation and emojis (VADER uses all three). Labels: positive if compound >= "
        f"{config.TEXT_SENTIMENT_THRESHOLD}, negative if <= -{config.TEXT_SENTIMENT_THRESHOLD}, else neutral.",
        "- Scored: usable comments only (`is_usable`: none of emoji-only, likely non-English, empty after "
        "cleaning, too short). Flagged rows stay in the output with the score **missing, not 0**.",
        "- Per product: `mean_compound`, label shares, and `like_weighted_mean_compound`, which weights each "
        "comment by 1 + its likes (so a comment with no likes still counts once).",
        "- Cohort and brand rows count a comment shared by several products of the group once.", "",
        "| | value |", "|---|--:|",
        f"| corpus rows | {len(comments):,} |",
        f"| unique comments | {len(unique):,} |",
        f"| scored rows (usable) | {int(comments['sentiment_compound'].notna().sum()):,} |",
        f"| scored unique comments | {n_scored:,} |",
        f"| not scored: too short / empty / emoji-only / non-English (rows; a row can have several) | "
        f"{int((reasons == 'too_short').sum()):,} / {int((reasons == 'empty_after_clean').sum()):,} / "
        f"{int((reasons == 'emoji_only').sum()):,} / {int((reasons == 'likely_non_english').sum()):,} |",
        f"| positive / neutral / negative (unique scored comments) | {_pct(shares['positive'])} / "
        f"{_pct(shares['neutral'])} / {_pct(shares['negative'])} |",
        f"| compound exactly 0 (no VADER sentiment word) | {zero:,} = {_pct(zero / n_scored if n_scored else np.nan)}"
        f" of scored, {_pct(zero / neutral if neutral else np.nan)} of neutral |",
        f"| products covered | {int((sent['scored_comments'] > 0).sum())} of {len(sent)} |", "",
        "By cohort:", "",
        "| cohort | products | scored comments | mean compound | positive | neutral | negative | like-weighted |",
        "|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for t in PRODUCT_TYPES:
        if t in types.index:
            r = types.loc[t]
            lines.append(f"| {t} | {r['products']} | {int(r['scored_comments']):,} | {_num(r['mean_compound'])} | "
                         f"{_pct(r['share_positive'])} | {_pct(r['share_neutral'])} | {_pct(r['share_negative'])} | "
                         f"{_num(r['like_weighted_mean_compound'])} |")
    lines += ["", "By brand (sorted by mean compound):", "",
              "| brand | products | scored comments | mean compound | negative |", "|---|--:|--:|--:|--:|"]
    for r in brands.itertuples():
        lines.append(f"| {r.group} | {r.products} | {r.scored_comments:,} | {_num(r.mean_compound)} | "
                     f"{_pct(r.share_negative)} |")
    if len(ranked):
        top, bottom = ranked.iloc[0], ranked.iloc[-1]
        lines += ["", f"Most positive product: {top['product_name']} ({_num(top['mean_compound'])}, "
                      f"{int(top['scored_comments'])} comments); least positive: {bottom['product_name']} "
                      f"({_num(bottom['mean_compound'])}, {int(bottom['scored_comments'])} comments). "
                      f"Spread of product means: {_num(ranked['mean_compound'].min())} to "
                      f"{_num(ranked['mean_compound'].max())}."]

    lines += ["", "## Topics", "",
              f"Documents: one per unique usable comment ({len(topics['docs']):,}); "
              f"{len(topics['docs']) - len(topics['modelled']):,} have no term left after the stop-lists and the "
              "`min_df`/`max_df` cut and are not modelled "
              f"(their topic columns are missing). Modelled: {len(topics['modelled']):,}.", "",
              "Vocabulary: the `tokens` column (SCRUM-23 stop-words already removed) minus",
              "- the domain stop-list `config.TEXT_TOPIC_STOP_WORDS`; each word with its share of usable comments:"]
    for group, words in stop_word_frequencies(corpus).items():
        lines.append(f"  - {group}: " + ", ".join(f"{w} {s:.1%}" for w, s in words))
    lines += [
        "- registry brand and model tokens, tokens containing a digit (`s24`, `x300`, `5g`) and one-letter tokens.",
        "- Terms in fewer than "
        f"{config.TEXT_TOPIC_MIN_DF:.2%} or more than {config.TEXT_TOPIC_MAX_DF:.0%} of documents "
        "(`min_df`, `max_df`).",
        "",
        "Decisions:",
        "- Sentiment words (good, best, love, great, nice) are **kept**: they appear in a few topics but do not "
        "take them over, and praise is itself a theme of these comments.",
    ]
    if len(branded):
        b = branded.set_index("method")
        m = main_sel[main_sel["selected_for_method"]].set_index("method")
        lines.append("- Brand and model words are **removed from the topic vocabulary** (they stay in `text_clean`). "
                     f"Kept, they made up {_pct(b.loc['lda', 'brand_term_share'])} of LDA's top terms and "
                     f"{_pct(b.loc['nmf', 'brand_term_share'])} of NMF's at the same k, and the topics became "
                     "brands (for example: " + b.loc["lda", "top_terms"].split(" | ")[0] + "). The product a comment "
                     "belongs to already says its brand, so brand topics add nothing; without them the topics are "
                     f"about what people discuss (coherence {_num(m.loc['lda', 'coherence_npmi'])} vs "
                     f"{_num(b.loc['lda', 'coherence_npmi'])} with brands kept; brand topics are coherent but "
                     "uninformative).")
    lines += [
        "", "Models: LDA (sklearn, batch, "
        f"{LDA_MAX_ITER} iterations) on word counts and NMF on TF-IDF, k = "
        f"{min(config.TEXT_TOPIC_K_RANGE)} to {max(config.TEXT_TOPIC_K_RANGE)}, random_state "
        f"{config.TEXT_TOPIC_RANDOM_STATE}. Scores per fit: coherence (mean NPMI of each topic's top 10 terms, "
        "from document co-occurrence; higher is better, 0 means the terms co-occur as often as chance), stability "
        f"(refit on {STABILITY_SUBSAMPLES} random 80% subsamples, mean best-match Jaccard of top-10 terms; 1 means "
        "identical topics) and overlap (mean Jaccard between topics; lower is more distinct). The k with the best "
        "average rank of coherence and stability is chosen per method (ties: smaller k). The reported topics are "
        "LDA's; NMF is the comparison.", "",
        "| method | k | coherence | stability | overlap | chosen |", "|---|--:|--:|--:|--:|:-:|",
    ]
    for r in main_sel.sort_values(["method", "k"]).itertuples():
        lines.append(f"| {r.method.upper()} | {r.k} | {_num(r.coherence_npmi)} | {_num(r.stability)} | "
                     f"{_num(r.topic_overlap)} | {'yes' if r.selected_for_method else ''} |")
    best = main_sel[main_sel["selected_for_method"]].set_index("method")
    lines += ["", f"Chosen: **LDA k={k_lda}** (NMF's best: k={k_nmf}). Read the terms below before trusting the "
                  "choice: the scores rank fits, they do not prove the topics are meaningful."]
    if {"lda", "nmf"} <= set(best.index):
        lda_b, nmf_b = best.loc["lda"], best.loc["nmf"]
        lines += ["", f"Honest reading: on comments this short (median 5 tokens) all fits score low. LDA's chosen fit "
                      f"has coherence {_num(lda_b['coherence_npmi'])} and stability {_num(lda_b['stability'])}: only "
                      "part of each topic's top terms comes back on a subsample. NMF's is more stable "
                      f"({_num(nmf_b['stability'])}, coherence {_num(nmf_b['coherence_npmi'])}) but its topics share "
                      f"more terms (overlap {_num(nmf_b['topic_overlap'])} vs {_num(lda_b['topic_overlap'])}). Compare "
                      "the LDA and NMF lists below: a theme both methods find is more trustworthy than any single "
                      "topic. Sparser LDA priors (doc-topic 0.05-0.1) were tried while building this and gave the "
                      "same themes, so sklearn's defaults are kept."]
    lines += ["",
              "| topic | suggested label (confirm) | share of comment text | dominant in | top 10 terms |",
              "|--:|---|--:|--:|---|"]
    for t, part in lda_terms.groupby("topic"):
        first = part.iloc[0]
        lines.append(f"| {t} | {first['label_suggestion']} | {_pct(first['topic_mean_share'])} | "
                     f"{int(first['dominant_comments']):,} | {' '.join(part.sort_values('rank')['term'])} |")
    nmf_terms = terms[terms["method"] == "nmf"]
    if len(nmf_terms):
        lines += ["", f"NMF (k={k_nmf}) for comparison:", ""]
        for t, part in nmf_terms.groupby("topic"):
            lines.append(f"- {t}: {' '.join(part.sort_values('rank')['term'])}")
    lines += ["", "Cohort topic shares (mean LDA share among comments with a topic):", "",
              "| topic | " + " | ".join(PRODUCT_TYPES) + " |", "|---|" + "--:|" * len(PRODUCT_TYPES)]
    for t, part in lda_terms.groupby("topic"):
        cells = [_pct(types.loc[p, f"topic_{t}_share"]) if p in types.index else "" for p in PRODUCT_TYPES]
        lines.append(f"| {t}: {part.iloc[0]['label_suggestion']} | " + " | ".join(cells) + " |")

    lines += ["", "## Aspects", "",
              "SCRUM-24's five keyword lists (`isolate_method2_data.DOMAIN_TOPIC_KEYWORDS`, imported, not copied), "
              f"without {', '.join(config.TEXT_ASPECT_EXCLUDED_KEYWORDS)} (`config.TEXT_ASPECT_EXCLUDED_KEYWORDS`): "
              "in these comments `video(s)` is almost always the YouTube video (it alone would put 11% of comments "
              "under camera), `power` is a model name (Poco X8 Power, Moto G Power), `light` is mostly "
              "\"background light\", and `buy`/`purchase` are buying intent, not price. A comment mentions an "
              "aspect if any token is in its list; aspect sentiment is the mean compound of the scored comments "
              "that mention it (the whole comment's score, not aspect-level sentiment).", "",
              "| aspect | " + " | ".join(PRODUCT_TYPES) + " |", "|---|" + "--:|" * len(PRODUCT_TYPES)]
    for name in ASPECTS:
        cells = [f"{_pct(types.loc[p, f'aspect_{name}_share'])} "
                 f"({_num(types.loc[p, f'aspect_{name}_mean_compound'], '{:+.2f}')})" if p in types.index else ""
                 for p in PRODUCT_TYPES]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "Cells: share of scored comments mentioning the aspect (mean compound of those comments)."]

    lines += ["", "## Link to search interest (descriptive)", "",
              f"Per product (`data/processed/{FULL_REPORT_IN}`): mean comment sentiment and comment volume against "
              "mean Google Trends interest over the 269-day window and the mean of its last 7 days.", ""]
    if len(link):
        lines += ["| text measure | Trends measure | n | Pearson r (p) | Spearman rho (p) | strength |",
                  "|---|---|--:|--:|--:|---|"]
        for r in link.itertuples():
            lines.append(f"| {r.text_measure} | {r.trends_measure} | {r.n_products} | {_num(r.pearson_r, '{:+.2f}')} "
                         f"({_num(r.pearson_p, '{:.2f}')}) | {_num(r.spearman_rho, '{:+.2f}')} "
                         f"({_num(r.spearman_p, '{:.2f}')}) | {r.strength_spearman} |")
        lines += ["", "Strength is read from |Spearman rho|: < 0.1 negligible, < 0.3 weak, < 0.5 moderate. With "
                      f"n = {int(link['n_products'].max())} products a correlation needs |rho| of about 0.25 to "
                      "reach p < 0.05, and even then it says nothing about cause. Comment volume is mostly the "
                      "sample design (at most 100 comments from each of 5 videos per snapshot; products with two "
                      "snapshots have more), not audience size."]
    else:
        lines.append(f"Not computed: `data/processed/{FULL_REPORT_IN}` is missing.")

    lines += ["", "## Limitations", "",
              "- **VADER is English-only.** Hinglish, other romanised Indian languages and Latin-script languages "
              "such as Indonesian pass the `likely_non_english` check (it judges the script, not the language) and "
              "are scored as English: most of their words are not in VADER's lexicon (bahut, acha, bekar, mast "
              f"are not), so they come out neutral. {_pct(zero_share)} of scored comments have compound exactly 0 "
              "(no sentiment word found); read the neutral share as \"no English sentiment detected\", not "
              "\"neutral opinion\". About "
              f"{int(indonesian.sum()):,} scored comments contain 2+ common Indonesian words (a rough count); "
              "they form their own topic above.",
              "- VADER was built for social media English; sarcasm, comparisons (\"the S25 is better than this\") "
              "and phone jargon are misread (\"heating\", \"drain\", \"overpriced\", \"hang\" are not in its "
              "lexicon; \"lag\" is). The "
              "spot-check file exists to measure this; no accuracy figure is claimed until it is labelled.",
              "- Generic words dominate the comments (\"phone\" is in 16% of them, \"video\" in 9%): without the "
              "domain stop-list the topics are about YouTube and brands, not phones. The stop-list is a judgement "
              "call, listed above with frequencies so it can be challenged.",
              f"- **Shared comments.** {shared:,} corpus rows are comments shared by 2+ products (one video in "
              "several products' search results). Per-product numbers include them for every product they belong "
              "to; cohort and brand numbers count them once; the topic models see each once.",
              "- The sample is narrow: top-level comments from each product's 5 most-viewed sampled videos, at most "
              "100 per video, in relevance order. It describes those videos' audiences, not all buyers.",
              "- Topic labels are keyword-based suggestions; confirm them by reading the terms and a few comments.",
              "", "## ⚠️ Leakage: descriptive only", "",
              "**The comments were collected on 2026-10-03/04** as a relevance-ranked sample of the videos most viewed "
              "by then. Every number here is a snapshot of that October sample. **Do not use these text outputs as "
              "time-series predictors for earlier dates** (a past day only has the comments that survived into the "
              "October sample). For modelling, use SCRUM-24's leak-free `processed/method2_train.csv` and "
              "`method2_test.csv`, whose text features are lagged and fitted on training-period comments only, and "
              "keep its caveats.", ""]
    return "\n".join(lines)


# --- main -----------------------------------------------------------------------------------------
def main(argv=None, paths=config.PATHS):
    parser = argparse.ArgumentParser(description="Descriptive sentiment and topic mining (SCRUM-28).")
    parser.add_argument("--no-plots", action="store_true", help="skip the charts")
    parser.add_argument("--ks", help="comma-separated k values to try (default: config.TEXT_TOPIC_K_RANGE)")
    args = parser.parse_args(argv)
    ks = tuple(int(k) for k in args.ks.split(",")) if args.ks else config.TEXT_TOPIC_K_RANGE

    if SentimentIntensityAnalyzer is None:
        print("vaderSentiment is not installed: pip3 install -r requirements.txt", file=sys.stderr)
        return 1
    in_path = paths.processed_dir / CORPUS_OUT
    if not in_path.exists() or in_path.stat().st_size == 0:
        print(f"Missing input {in_path}. Run src/build_text_corpus.py first.", file=sys.stderr)
        return 1
    registry = read_products(paths.active_registry_csv)
    protected = protected_terms(registry)
    corpus = pd.read_csv(in_path, dtype=str, keep_default_na=False, encoding="utf-8")

    topics = run_topic_models(corpus, protected, ks, config.TEXT_TOPIC_MIN_DF, config.TEXT_TOPIC_MAX_DF)
    terms = topic_terms_table(topics)
    comments = build_comments(corpus, topics)
    topic_cols = [c for c in comments.columns if c.startswith("topic_") and c[6:].isdigit()]
    sent = per_product(comments, registry, sentiment_stats)
    topics_by_product = per_product(comments, registry, lambda f: topic_aspect_stats(f, topic_cols))
    groups = by_group(comments, registry, topic_cols)

    report_path = paths.processed_dir / FULL_REPORT_IN
    if report_path.exists() and report_path.stat().st_size:
        link, merged = signal_link(sent, pd.read_csv(report_path, dtype=str, keep_default_na=False))
    else:
        link, merged = pd.DataFrame(), pd.DataFrame()

    out = paths.processed_dir

    def write(df, name):
        round_floats(df).to_csv(out / name, index=False, lineterminator="\n")

    write(comments.drop(columns="comment_key"), COMMENTS_OUT)
    write(sent, SENTIMENT_BY_PRODUCT_OUT)
    write(terms, TOPIC_TERMS_OUT)
    write(topics_by_product, TOPICS_BY_PRODUCT_OUT)
    write(topics["selection"], MODEL_SELECTION_OUT)
    write(groups, BY_GROUP_OUT)
    write(link, SIGNAL_LINK_OUT)
    spot_path = out / SPOTCHECK_OUT
    if not spotcheck_has_labels(spot_path):
        spotcheck(comments, corpus).to_csv(spot_path, index=False, lineterminator="\n")
    spot_note = (f"{len(pd.read_csv(spot_path))} scored comments drawn at random (seed "
                 f"{config.TEXT_TOPIC_RANDOM_STATE}) with the VADER label and an empty `human_label` column for "
                 "real human labels. The script never overwrites it once any of "
                 f"{', '.join(SPOTCHECK_LABEL_COLUMNS)} is filled in; delete it to draw a new sample.")

    docs = paths.root / "docs"
    docs.mkdir(exist_ok=True)
    doc_path = docs / SUMMARY_DOC
    block = eval_block(doc_path)
    doc_path.write_text(summary_markdown(corpus, comments, sent, groups, topics, terms, link, spot_note)
                        + ("\n" + block if block else ""), encoding="utf-8")
    if not args.no_plots:
        make_plots(comments, sent, groups, terms, link, merged, docs / "figures")

    unique = comments.drop_duplicates("comment_key")
    scored = unique["sentiment_label"][unique["sentiment_compound"].notna()]
    print(f"{COMMENTS_OUT}: {len(comments)} rows, {int(comments['sentiment_compound'].notna().sum())} scored, "
          f"{sent['product_id'].nunique()} products")
    print("labels (unique scored comments): " + ", ".join(f"{lab} {(scored == lab).mean():.1%}" for lab in LABELS))
    print(f"topics: LDA k={topics['chosen'].get('lda')}, NMF k={topics['chosen'].get('nmf')}; "
          f"summary: docs/{SUMMARY_DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
