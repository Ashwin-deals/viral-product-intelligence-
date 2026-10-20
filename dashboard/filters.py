"""Global filter logic shared by every dashboard page.

Pure pandas, no Streamlit, so it is unit-tested directly. The sidebar in app.py builds a
FilterState; pages call apply_filters() on whichever dataset they plot.

Filters cascade: category narrows the brand options, category + brands narrow the cohort
options, and all three narrow the product options. An empty selection means "all".
"""
from dataclasses import dataclass
from datetime import date

import pandas as pd

ALL = "All"
COHORT_ORDER = ["RISING", "STABLE", "DECLINING"]


@dataclass(frozen=True)
class FilterState:
    category: str = ALL
    brands: tuple = ()
    cohorts: tuple = ()
    products: tuple = ()  # product_ids
    date_range: tuple | None = None  # (start, end) as datetime.date, both inclusive


def _narrow(catalog, category=ALL, brands=(), cohorts=()):
    mask = pd.Series(True, index=catalog.index)
    if category and category != ALL:
        mask &= catalog["category"] == category
    if brands:
        mask &= catalog["brand"].isin(brands)
    if cohorts:
        mask &= catalog["product_type"].isin(cohorts)
    return catalog[mask]


def category_options(catalog):
    return [ALL] + sorted(catalog["category"].dropna().unique())


def brand_options(catalog, category=ALL):
    return sorted(_narrow(catalog, category)["brand"].dropna().unique())


def cohort_options(catalog, category=ALL, brands=()):
    present = set(_narrow(catalog, category, brands)["product_type"].dropna())
    return [c for c in COHORT_ORDER if c in present] + sorted(present - set(COHORT_ORDER))


def product_options(catalog, category=ALL, brands=(), cohorts=()):
    return _narrow(catalog, category, brands, cohorts)["product_id"].tolist()


def product_label(catalog):
    names = dict(zip(catalog["product_id"], catalog["product_name"]))
    return lambda pid: f"{pid} · {names.get(pid, pid)}"


def selected_product_ids(catalog, state):
    ids = product_options(catalog, state.category, state.brands, state.cohorts)
    if state.products:
        chosen = set(state.products)
        ids = [pid for pid in ids if pid in chosen]
    return ids


def date_bounds(*frames, column="date"):
    """(min, max) date across the frames that have the column, or None if none has data."""
    lows, highs = [], []
    for df in frames:
        if column in df.columns and not df.empty:
            values = pd.to_datetime(df[column]).dropna()
            if not values.empty:
                lows.append(values.min())
                highs.append(values.max())
    if not lows:
        return None
    return min(lows).date(), max(highs).date()


def apply_filters(df, state, catalog, date_col="date"):
    """Rows of df for the selected products and inside the selected date range."""
    if df.empty:
        return df
    out = df
    if "product_id" in out.columns:
        out = out[out["product_id"].isin(selected_product_ids(catalog, state))]
    if state.date_range and date_col in out.columns:
        start, end = (pd.Timestamp(d) for d in state.date_range)
        dates = pd.to_datetime(out[date_col])
        out = out[(dates >= start) & (dates <= end)]
    return out


def describe(state, catalog):
    """One-line human summary of the active filters."""
    parts = [f"{len(selected_product_ids(catalog, state))} of {len(catalog)} products"]
    if state.category != ALL:
        parts.append(f"category {state.category}")
    if state.brands:
        parts.append("brands " + ", ".join(state.brands))
    if state.cohorts:
        parts.append("cohorts " + ", ".join(state.cohorts))
    if state.date_range:
        start, end = state.date_range
        parts.append(f"{_fmt(start)} → {_fmt(end)}")
    return " · ".join(parts)


def _fmt(d):
    return d.isoformat() if isinstance(d, date) else str(d)
