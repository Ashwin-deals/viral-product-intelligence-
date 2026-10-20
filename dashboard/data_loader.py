"""Read-only loaders for the dashboard.

Every dataset comes from files the pipeline already writes (see config.Paths). A missing file
loads as an empty DataFrame so a page can show a notice instead of crashing.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402

CATALOG_COLUMNS = ["product_id", "product_name", "brand", "category", "product_type", "launch_period"]


@dataclass(frozen=True)
class Datasets:
    catalog: pd.DataFrame  # one row per registry product (active registry version)
    master: pd.DataFrame  # processed/master_dataset.csv, one row per product and day
    forecasts: pd.DataFrame  # Method 1 test-period predictions
    signals: pd.DataFrame  # Method 2 daily sentiment/topic signals
    model_comparison: pd.DataFrame
    feature_importance: pd.DataFrame


def read_csv(path, parse_dates=("date",)):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    for col in parse_dates:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def load_catalog(paths):
    catalog = read_csv(paths.registry_csv(config.ACTIVE_REGISTRY), parse_dates=())
    if catalog.empty:
        return pd.DataFrame(columns=CATALOG_COLUMNS)
    return catalog[CATALOG_COLUMNS].drop_duplicates("product_id").sort_values("product_id").reset_index(drop=True)


def load_datasets(paths=None):
    paths = paths or config.PATHS
    return Datasets(
        catalog=load_catalog(paths),
        master=read_csv(paths.master_dataset_csv),
        forecasts=read_csv(paths.method1_forecast_predictions_csv),
        signals=read_csv(paths.method2_daily_signals_csv),
        model_comparison=read_csv(paths.method1_model_comparison_csv, parse_dates=()),
        feature_importance=read_csv(paths.method1_feature_importance_csv, parse_dates=()),
    )
