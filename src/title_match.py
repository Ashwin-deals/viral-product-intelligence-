"""Does a video title name the exact model, a sibling model, or neither?

Used by the pilot report (on-target share) and by the recent-window pass
(videos_7d_on_target). Titles from search.list snippets are HTML-escaped; they are unescaped
first.
"""

import html
import re

# Words that, right after the model phrase, mean a sibling model (e.g. "vivo t5" + "pro").
VARIANT_WORDS = {"pro", "max", "plus", "ultra", "lite", "fusion", "neo", "fold", "xl", "fe", "mini",
                 "power", "prime", "edge", "flip", "air", "duo", "c", "e", "r", "s", "x"}


def normalize_title(title):
    title = html.unescape(title).lower().replace("+", " plus ")
    return re.sub(r"[^a-z0-9]+", " ", title).strip()


def model_phrase(query):
    """Regex for the model in a title. Queries of 3+ words drop the leading brand/series word
    ("galaxy z flip 8" -> "z flip 8", "motorola edge 70" -> "edge 70") because titles often omit
    it, and spaces between words are optional ("flip8", "edge70")."""
    tokens = normalize_title(query).split()
    core = tokens[1:] if len(tokens) >= 3 else tokens
    return r"\s*".join(re.escape(t) for t in core)


def title_classifier(query):
    """Return a function title -> 'on_target' | 'sibling' | 'off_topic' for a trends_query."""
    phrase = model_phrase(query)
    exact = re.compile(rf"\b{phrase}\b(?!\s+(?:{'|'.join(sorted(VARIANT_WORDS))})\b)")
    any_mention = re.compile(rf"\b{phrase}\b")

    def classify(title):
        t = normalize_title(title)
        if exact.search(t):
            return "on_target"
        if any_mention.search(t):
            return "sibling"
        return "off_topic"

    return classify


def classify_titles(query, titles):
    """Share of titles that name the exact model, a sibling model, or neither."""
    classify = title_classifier(query)
    counts = {"on_target": 0, "sibling": 0, "off_topic": 0}
    for title in titles:
        counts[classify(title)] += 1
    total = max(len(titles), 1)
    return {k: v / total for k, v in counts.items()}
