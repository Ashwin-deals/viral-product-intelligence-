import csv

import pytest

import config

PRODUCTS = [
    {"product_id": "P001", "product_name": "Apple iPhone 18 Pro", "brand": "Apple", "category": "Smartphones",
     "trends_query": "iphone 18 pro", "youtube_query": "iphone 18 pro review", "launch_period": "2026-09",
     "product_type": "RISING", "notes": ""},
    {"product_id": "P002", "product_name": "Google Pixel 11", "brand": "Google", "category": "Smartphones",
     "trends_query": "pixel 11", "youtube_query": "pixel 11 review", "launch_period": "2026-08",
     "product_type": "RISING", "notes": ""},
]


@pytest.fixture
def paths(tmp_path):
    """A throwaway repo layout with a two-product registry and an empty collection log."""
    p = config.Paths.from_root(tmp_path)
    p.data_dir.mkdir()
    for version in config.REGISTRY_FILES:  # identical registry versions unless a test changes one
        write_products(p.registry_csv(version), PRODUCTS)
    p.collection_log_csv.touch()
    return p


def write_products(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(PRODUCTS[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def products():
    return [dict(p) for p in PRODUCTS]


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
