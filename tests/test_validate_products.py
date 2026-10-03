import csv

import validate_products as vp
from conftest import PRODUCTS, write_products

BASE = [dict(p, product_id=f"P00{i}") for i, p in enumerate(PRODUCTS, start=1)]


def test_repository_registries_pass():
    assert vp.main([]) == 0  # every version in config.REGISTRY_FILES, plus cross-version checks


def test_exclusions_allowed_but_not_other_query_changes(tmp_path):
    good = tmp_path / "good.csv"
    write_products(good, [dict(BASE[0], youtube_query="iphone 18 pro review -max -ultra"), BASE[1]])
    assert vp.main([str(good)]) == 0
    bad = tmp_path / "bad.csv"
    write_products(bad, [dict(BASE[0], youtube_query="iphone 18 pro unboxing"), BASE[1]])
    assert vp.main([str(bad)]) == 1


def test_versions_may_only_change_youtube_query(tmp_path):
    v1, v2, v3 = (tmp_path / f"{n}.csv" for n in ("v1", "v2", "v3"))
    write_products(v1, BASE)
    write_products(v2, [dict(BASE[0], youtube_query="iphone 18 pro review -max"), BASE[1]])
    write_products(v3, [dict(BASE[0], trends_query="iphone 18"), BASE[1]])
    problems, changes = vp.check_versions([("v1", v1), ("v2", v2)])
    assert problems == [] and changes == {"v2": ["P001"]}
    problems, _ = vp.check_versions([("v1", v1), ("v3", v3)])
    assert any("trends_query changed" in p for p in problems)
