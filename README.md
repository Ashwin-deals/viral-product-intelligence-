# Viral Product Intelligence

Early Detection and Prediction of Consumer Demand Surges Using Multi-Source Signals (Business Analytics capstone).

## Data files

All CSV files live in `data/`.

| File | Purpose |
|------|---------|
| `data/products.csv` | Product registry (working copy): smartphones for the India market, with the fixed Google Trends and YouTube search queries used for collection. |
| `data/products_v1_2026-10-03.csv` | Frozen copy of the product registry as of 2026-10-03. Do not edit; save a new dated version if the list changes. |
| `data/collection_log.csv` | Log of data-collection runs. |
| `data/source_log.csv` | Log of data sources used. |
| `data/data_dictionary.csv` | Definitions of the columns used across the project's datasets. |

### Product registry conventions

- `product_id`: `P001`, `P002`, ... sequential, never reused.
- `brand`: the brand the phone is sold under in India (Redmi and Poco are Xiaomi sub-brands; iQOO is a vivo sub-brand).
- `trends_query`: lowercase Google Trends search term. `youtube_query` is always `<trends_query> review`.
- `launch_period`: India launch month (`YYYY-MM`), `established` if launched more than a year before 2026-10-03, or `unverified`.
- `product_type`: `RISING` (launched since mid-2026 or upcoming), `STABLE` (current, steady interest), `DECLINING` (superseded or older model).

Validate the registry with:

```
python3 src/validate_products.py
```
