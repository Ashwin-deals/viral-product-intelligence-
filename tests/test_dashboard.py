from datetime import date

import pandas as pd
import pytest

import filters as flt

CATALOG = pd.DataFrame([
    {"product_id": "P001", "product_name": "Phone A", "brand": "Apple", "category": "Smartphones", "product_type": "RISING"},
    {"product_id": "P002", "product_name": "Phone B", "brand": "Apple", "category": "Smartphones", "product_type": "STABLE"},
    {"product_id": "P003", "product_name": "Phone C", "brand": "Samsung", "category": "Smartphones", "product_type": "DECLINING"},
    {"product_id": "P004", "product_name": "Tab D", "brand": "Samsung", "category": "Tablets", "product_type": "RISING"},
])


def daily(ids, start="2026-01-01", days=5):
    dates = pd.date_range(start, periods=days)
    return pd.DataFrame([{"product_id": pid, "date": d, "value": 1} for pid in ids for d in dates])


def test_category_options_lead_with_all():
    assert flt.category_options(CATALOG) == [flt.ALL, "Smartphones", "Tablets"]


def test_options_cascade_from_category_to_brand_to_cohort_to_product():
    assert flt.brand_options(CATALOG, "Tablets") == ["Samsung"]
    assert flt.brand_options(CATALOG) == ["Apple", "Samsung"]
    assert flt.cohort_options(CATALOG, "Smartphones", ("Samsung",)) == ["DECLINING"]
    assert flt.cohort_options(CATALOG) == ["RISING", "STABLE", "DECLINING"]
    assert flt.product_options(CATALOG, "Smartphones", ("Apple",), ("RISING",)) == ["P001"]


def test_empty_selection_means_all_products():
    assert flt.selected_product_ids(CATALOG, flt.FilterState()) == ["P001", "P002", "P003", "P004"]


def test_explicit_products_are_intersected_with_upstream_filters():
    state = flt.FilterState(brands=("Apple",), products=("P001", "P003"))
    assert flt.selected_product_ids(CATALOG, state) == ["P001"]


def test_apply_filters_limits_products_and_inclusive_dates():
    df = daily(["P001", "P002", "P003"])
    state = flt.FilterState(cohorts=("RISING", "STABLE"), date_range=(date(2026, 1, 2), date(2026, 1, 4)))
    out = flt.apply_filters(df, state, CATALOG)
    assert sorted(out["product_id"].unique()) == ["P001", "P002"]
    assert out["date"].min() == pd.Timestamp("2026-01-02")
    assert out["date"].max() == pd.Timestamp("2026-01-04")
    assert len(out) == 2 * 3


def test_apply_filters_handles_empty_frame_and_frames_without_product_id():
    state = flt.FilterState(date_range=(date(2026, 1, 1), date(2026, 1, 1)))
    assert flt.apply_filters(pd.DataFrame(), state, CATALOG).empty
    frame = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=3)})
    assert len(flt.apply_filters(frame, state, CATALOG)) == 1


def test_date_bounds_span_all_frames_and_skip_frames_without_dates():
    bounds = flt.date_bounds(daily(["P001"], "2026-01-03"), daily(["P002"], "2026-01-01", 2), pd.DataFrame())
    assert bounds == (date(2026, 1, 1), date(2026, 1, 7))
    assert flt.date_bounds(pd.DataFrame()) is None


def test_describe_summarises_active_filters():
    state = flt.FilterState(category="Smartphones", brands=("Apple",), date_range=(date(2026, 1, 1), date(2026, 2, 1)))
    text = flt.describe(state, CATALOG)
    assert text.startswith("2 of 4 products")
    assert "category Smartphones" in text and "brands Apple" in text and "2026-01-01 → 2026-02-01" in text


def test_product_label_falls_back_to_id():
    label = flt.product_label(CATALOG)
    assert label("P001") == "P001 · Phone A"
    assert label("P999") == "P999 · P999"


# --- Whole-app smoke tests (headless Streamlit, real repo data) -----------------------------

APP = str(flt.__file__).replace("filters.py", "app.py")
PAGES = ["views/overview.py", "views/radar.py", "views/trends.py", "views/forecasts.py", "views/signals.py"]


@pytest.fixture
def app():
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def _sidebar(at, kind, label):
    return next(w for w in getattr(at.sidebar, kind) if w.label == label)


def test_app_renders_every_page_with_default_filters(app):
    for page in PAGES:
        app.switch_page(page).run()
        assert not app.exception, (page, app.exception)
        assert app.title, page


def test_global_filters_apply_on_every_page(app):
    _sidebar(app, "multiselect", "Brand").set_value(["Samsung"]).run()
    _sidebar(app, "multiselect", "Trend cohort").set_value(["RISING"]).run()
    _sidebar(app, "slider", "Date range").set_value((date(2026, 8, 15), date(2026, 9, 30))).run()
    assert not app.exception, app.exception

    for page in PAGES:
        app.switch_page(page).run()
        assert not app.exception, (page, app.exception)
        caption = app.caption[0].value
        assert "brands Samsung" in caption and "cohorts RISING" in caption, (page, caption)
        assert "2026-08-15 → 2026-09-30" in caption, (page, caption)


def test_products_outside_upstream_filters_are_dropped(app):
    _sidebar(app, "multiselect", "Products").set_value(["P001"]).run()
    _sidebar(app, "multiselect", "Brand").set_value(["Samsung"]).run()
    assert not app.exception, app.exception
    assert _sidebar(app, "multiselect", "Products").value == []


def test_reset_restores_defaults(app):
    _sidebar(app, "multiselect", "Brand").set_value(["Apple"]).run()
    _sidebar(app, "button", "Reset filters").click().run()
    assert not app.exception, app.exception
    assert _sidebar(app, "multiselect", "Brand").value == []
    assert app.caption[0].value.startswith("60 of 60 products")


def test_radar_dataset_computation():
    import sys
    from pathlib import Path
    dashboard_dir = Path(APP).parent
    if str(dashboard_dir) not in sys.path:
        sys.path.insert(0, str(dashboard_dir))
    from views.radar import compute_radar_dataset

    # Empty frame returns empty
    assert compute_radar_dataset(pd.DataFrame(), pd.DataFrame(), CATALOG).empty

    # Synthetic master frame
    master = pd.DataFrame([
        {"product_id": "P001", "product_name": "Phone A", "brand": "Apple", "product_type": "RISING",
         "search_interest": 75.0, "search_interest_growth": 0.25, "search_interest_accel": 0.10,
         "surge_label": 1, "date": pd.Timestamp("2026-08-15")},
        {"product_id": "P002", "product_name": "Phone B", "brand": "Apple", "product_type": "STABLE",
         "search_interest": 40.0, "search_interest_growth": -0.05, "search_interest_accel": -0.02,
         "surge_label": 0, "date": pd.Timestamp("2026-08-15")},
    ])
    signals = pd.DataFrame([
        {"product_id": "P001", "comments_published": 15, "sentiment_volume": 15,
         "sentiment_mean": 0.35, "sentiment_pos_ratio": 0.70, "sentiment_neg_ratio": 0.10, "sentiment_net_polarity": 0.60},
        {"product_id": "P002", "comments_published": 5, "sentiment_volume": 5,
         "sentiment_mean": 0.05, "sentiment_pos_ratio": 0.40, "sentiment_neg_ratio": 0.20, "sentiment_net_polarity": 0.20},
    ])

    radar_df = compute_radar_dataset(master, signals, CATALOG)
    assert len(radar_df) == 2
    assert "breakout_score" in radar_df.columns
    assert "quadrant_tier" in radar_df.columns
    assert "recommended_action" in radar_df.columns
    assert (radar_df["breakout_score"] >= 0.0).all() and (radar_df["breakout_score"] <= 100.0).all()
    # P001 with higher interest, accel and sentiment should have higher breakout score than P002
    assert radar_df.loc[radar_df["product_id"] == "P001", "breakout_score"].iloc[0] > \
           radar_df.loc[radar_df["product_id"] == "P002", "breakout_score"].iloc[0]

