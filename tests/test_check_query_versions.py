import json

import check_query_versions as cqv
from conftest import PRODUCTS, read_csv, write_products
from common import append_rows


def raw(paths, name, doc):
    paths.raw_youtube_dir.mkdir(parents=True, exist_ok=True)
    (paths.raw_youtube_dir / name).write_text(json.dumps(doc))


def search_doc(q, version=None):
    doc = {"request_params": {"q": q}, "responses": [{"items": [], "pageInfo": {"totalResults": 5}}]}
    if version:
        doc["query_version"] = version
    return doc


def test_raw_files_checked_against_registry(paths, capsys):
    write_products(paths.registry_csv("v2"), [dict(PRODUCTS[0], youtube_query="iphone 18 pro review -max"),
                                               PRODUCTS[1]])
    raw(paths, "P001_2026-10-03_search.json", search_doc("iphone 18 pro review"))  # untagged = v1, matches
    raw(paths, "P001_2026-10-03_videos.json", {"request_params": {}, "responses": []})
    raw(paths, "P001_2026-10-03_qv2_search.json", search_doc("iphone 18 pro review -max", "v2"))
    raw(paths, "P002_2026-10-03_recent7d.json", search_doc("wrong query"))  # mismatch
    raw(paths, "P002_2026-10-03_comments.json", {"request_params": {}, "videos": []})  # no search file

    assert cqv.main(paths) == 1

    index = {r["file"]: r for r in read_csv(paths.youtube_data_dir / "raw_file_index.csv")}
    assert index["P001_2026-10-03_search.json"]["version_source"] == "untagged_v1_convention"
    assert index["P001_2026-10-03_search.json"]["status"] == "ok"
    assert index["P001_2026-10-03_qv2_search.json"]["query_version"] == "v2"
    assert index["P001_2026-10-03_qv2_search.json"]["status"] == "ok"
    assert index["P001_2026-10-03_videos.json"]["status"] == "ok"
    assert "!= registry v1" in index["P002_2026-10-03_recent7d.json"]["status"]
    assert "no matching P002_2026-10-03_search.json" in index["P002_2026-10-03_comments.json"]["status"]


def test_tables_backfilled_and_rows_need_raw_files(paths):
    raw(paths, "P001_2026-10-03_search.json", search_doc("iphone 18 pro review"))
    old_header = ["snapshot_date", "product_id", "video_result_count", "video_views_total", "video_comments_total"]
    append_rows(paths.product_daily_csv, old_header, [
        {"snapshot_date": "2026-10-03", "product_id": "P001", "video_result_count": 1,
         "video_views_total": 1, "video_comments_total": 1}])

    assert cqv.main(paths) == 0
    assert read_csv(paths.product_daily_csv)[0]["query_version"] == "v1"

    append_rows(paths.product_daily_csv, list(read_csv(paths.product_daily_csv)[0]), [
        {"snapshot_date": "2026-10-04", "product_id": "P001", "query_version": "v1", "video_result_count": 1,
         "video_views_total": 1, "video_comments_total": 1, "search_total_results_approx": "",
         "result_cap_hit": ""}])
    assert cqv.main(paths) == 1  # no raw search file for 2026-10-04
