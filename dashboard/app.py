"""Viral Product Intelligence dashboard: Streamlit multi-page entry point.

Run from the repo root:

    streamlit run dashboard/app.py

The sidebar filters (category, brand, trend cohort, products, date range) live here in the
entry point, so they persist across page switches and every page (dashboard/views/*.py)
receives the same PageContext. Pages only render; filtering is done by dashboard/filters.py.
"""
import sys
from pathlib import Path

import streamlit as st

DASHBOARD_DIR = Path(__file__).resolve().parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import context  # noqa: E402
import filters as flt  # noqa: E402
from data_loader import load_datasets  # noqa: E402

KEYS = {
    "category": "flt_category",
    "brands": "flt_brands",
    "cohorts": "flt_cohorts",
    "products": "flt_products",
    "dates": "flt_dates",
}

PAGES = [  # (script relative to this file, title, icon); the first one is the default page
    ("views/overview.py", "Overview", ":material/dashboard:"),
    ("views/trends.py", "Search Trends", ":material/trending_up:"),
    ("views/forecasts.py", "Forecasts", ":material/query_stats:"),
    ("views/signals.py", "Audience Signals", ":material/forum:"),
]


@st.cache_data(show_spinner="Loading datasets…")
def get_datasets():
    return load_datasets()


def reset_filters():
    for key in KEYS.values():
        st.session_state.pop(key, None)


def _keep_valid(key, options):
    """Drop selections that an upstream filter has made unavailable (before the widget is drawn)."""
    if key in st.session_state:
        st.session_state[key] = [v for v in st.session_state[key] if v in options]


def render_filters(data):
    catalog = data.catalog
    sidebar = st.sidebar
    sidebar.header("Global filters")
    sidebar.caption("Applied to every page. Empty selections mean *all*.")

    categories = flt.category_options(catalog)
    if st.session_state.get(KEYS["category"]) not in categories:
        st.session_state[KEYS["category"]] = flt.ALL
    category = sidebar.selectbox("Category", categories, key=KEYS["category"])

    brand_opts = flt.brand_options(catalog, category)
    _keep_valid(KEYS["brands"], brand_opts)
    brands = sidebar.multiselect("Brand", brand_opts, key=KEYS["brands"], placeholder="All brands")

    cohort_opts = flt.cohort_options(catalog, category, brands)
    _keep_valid(KEYS["cohorts"], cohort_opts)
    cohorts = sidebar.multiselect(
        "Trend cohort", cohort_opts, key=KEYS["cohorts"], placeholder="All cohorts",
        help="Registry product_type: expected demand trajectory over the tracking window.",
    )

    product_opts = flt.product_options(catalog, category, brands, cohorts)
    _keep_valid(KEYS["products"], product_opts)
    products = sidebar.multiselect(
        "Products", product_opts, key=KEYS["products"], format_func=flt.product_label(catalog),
        placeholder="All products matching the filters above",
    )

    date_range = None
    bounds = flt.date_bounds(data.master, data.forecasts, data.signals)
    if bounds:
        low, high = bounds
        current = st.session_state.get(KEYS["dates"])
        if not current or current[0] < low or current[1] > high:
            st.session_state[KEYS["dates"]] = (low, high)
        date_range = tuple(sidebar.slider(
            "Date range", min_value=low, max_value=high, key=KEYS["dates"], format="YYYY-MM-DD",
        ))

    sidebar.button("Reset filters", on_click=reset_filters, icon=":material/restart_alt:", width="stretch")

    return flt.FilterState(
        category=category, brands=tuple(brands), cohorts=tuple(cohorts),
        products=tuple(products), date_range=date_range,
    )


def main():
    st.set_page_config(page_title="Viral Product Intelligence", page_icon=":material/insights:", layout="wide")
    data = get_datasets()
    if data.catalog.empty:
        st.error("The product registry could not be loaded.")
        st.stop()

    context.publish(context.PageContext(data=data, state=render_filters(data)))
    st.sidebar.divider()
    st.sidebar.caption("Smartphones · India · Google Trends + YouTube Data API v3")

    pages = [
        st.Page(str(DASHBOARD_DIR / script), title=title, icon=icon, default=(i == 0))
        for i, (script, title, icon) in enumerate(PAGES)
    ]
    st.navigation(pages).run()


main()
