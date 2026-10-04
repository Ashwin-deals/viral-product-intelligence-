"""Pick the pilot products from data/products.csv and write data/pilot_products.csv.

Selection is deterministic (fixed seed): within each product_type the rows are shuffled,
then picked greedily so that brands already chosen are used again only when no unused
brand is left. Products with launch_period "unverified" (not confirmed as launched in
India) are excluded, since they may have no data to collect yet.

Usage: python src/select_pilot.py
"""

import csv
import random
import sys

import config
from common import read_products


def select_pilot(products, counts=config.PILOT_COUNTS, seed=config.PILOT_SEED):
    rng = random.Random(seed)
    eligible = [p for p in products if p["launch_period"] != "unverified"]
    used_brands = set()
    chosen = []
    for product_type, n in counts.items():
        pool = [p for p in eligible if p["product_type"] == product_type]
        if len(pool) < n:
            raise ValueError(f"only {len(pool)} eligible {product_type} products, need {n}")
        rng.shuffle(pool)
        picked = []
        while len(picked) < n:
            fresh = [p for p in pool if p["brand"] not in used_brands and p not in picked]
            candidate = fresh[0] if fresh else next(p for p in pool if p not in picked)
            picked.append(candidate)
            used_brands.add(candidate["brand"])
        chosen.extend(picked)
    return sorted(chosen, key=lambda p: p["product_id"])


def main(paths=config.PATHS):
    products = read_products(paths.products_csv)
    pilot = select_pilot(products)
    with open(paths.pilot_products_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(products[0].keys()))
        writer.writeheader()
        writer.writerows(pilot)

    print(f"Pilot products (seed {config.PILOT_SEED}) written to {paths.pilot_products_csv}:\n")
    for p in pilot:
        print(f"  {p['product_id']}  {p['product_type']:<9}  {p['brand']:<9}  {p['product_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
