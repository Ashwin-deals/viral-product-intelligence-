# Product registry changelog

Registry versions are separate files in `data/`. The collector reads the version set by `ACTIVE_REGISTRY` in `src/config.py`. Earlier versions and the frozen copy (`products_v1_2026-10-03.csv`) are never edited.

| Version | File | Status |
|---------|------|--------|
| v1 | `data/products.csv` (frozen copy `data/products_v1_2026-10-03.csv`) | original registry, 60 products |
| v2 | `data/products_v2.csv` | **active** since 2026-10-03 |

Raw YouTube files collected with a query that first appeared in version vN (N > 1) carry a `_qvN` tag, e.g. `raw/youtube/P044_2026-10-03_qv2_search.json`. Extract rows have a `query_version` column, so collections made with different queries never overwrite or deduplicate each other.

## v2 (2026-10-03)

Only `youtube_query` changed, for two products. `trends_query` and every other column are identical to v1.

| product_id | v1 youtube_query | v2 youtube_query |
|------------|------------------|------------------|
| P044 Motorola Edge 70 | `motorola edge 70 review` | `motorola edge 70 review -fusion -pro -max -neo` |
| P057 Google Pixel 9 | `pixel 9 review` | `pixel 9 review -pro -xl -fold -9a` |

**Why:** in the pilot (snapshot 2026-10-03), about 42% of video titles for both products named a sibling model (Edge 70 Fusion/Pro/Max; Pixel 9 Pro/Pro XL/Fold), so view and comment totals mixed models. The search.list documentation says `q` supports the Boolean NOT operator (`-term`), so v2 excludes the sibling names.

**Result** (same day, 50 videos per query, measured with `src/pilot_report.py`):

| Product | Query | On-target | Sibling | Neither |
|---------|-------|----------:|--------:|--------:|
| P044 | v1 | 56% | 42% | 2% |
| P044 | v2 | 44% | 52% | 4% |
| P057 | v1 | 58% | 42% | 0% |
| P057 | v2 | 64% | 18% | 18% |

- **P044: worse.** YouTube still returned titles that say "Edge 70 Pro", "Fusion" and "Max". The NOT operator did not filter titles in practice.
- **P057: only a small improvement.** Pro and Pro XL titles fell from 42% to 18%, but Pixel 9a videos (counted under "Neither") rose to 18% despite `-9a`. The share of titles not naming the exact model fell only from 42% to 36%.
- **Conclusion:** neither narrowed query clearly fixes the problem. v2 stays active so the experiment is reproducible, but the team should decide whether to revert to v1 or replace the products (below).

**Possibly cleaner replacements already in the registry (not applied):**
- **Motorola:** P022 Motorola Edge 70 Max (RISING). Its query ends in a distinctive variant word, so fewer siblings contain it. It is the only other Motorola product in the registry.
- **Google:**
  - P033 Google Pixel 10a (STABLE): the distinctive "10a" suffix has no longer sibling.
  - P056 Google Pixel 10 Pro (DECLINING): same type as P057, but its query also matches Pixel 10 Pro XL and Pro Fold, so it is probably not cleaner.
  - Top-of-line names have no longer siblings and are likely cleaner DECLINING choices, but none is in the Google family.
- None of these has been measured. Checking each costs 1 search.list call plus a few units.
