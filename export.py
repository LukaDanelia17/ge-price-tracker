"""Export products and price_changes to CSV for analysis outside the repo.

    .venv/bin/python export.py                 # writes exports/*.csv
    .venv/bin/python export.py --out ~/Desktop

Times are Tbilisi local ("2026-11-09 14:36:21"). Files are UTF-8 with a BOM
so Excel shows Georgian product names correctly. DATABASE_URL comes from the
environment, or from .env next to this file.
"""

import argparse
import os
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent

TS = "to_char({col} at time zone 'Asia/Tbilisi', 'YYYY-MM-DD HH24:MI:SS')"

QUERIES = {
    "products.csv": f"""
        select id, store, store_product_id, name, url, category,
               {TS.format(col="first_seen")} as first_seen,
               {TS.format(col="last_seen")} as last_seen
        from products
        order by store, id
    """,
    "price_changes.csv": f"""
        select c.id, c.product_id, p.store, p.store_product_id, p.category,
               {TS.format(col="c.observed_at")} as observed_at,
               c.price, c.old_price,
               -- true/false instead of Postgres's t/f; empty in_stock = unknown (zoommer)
               c.in_stock::text as in_stock, c.buyable::text as buyable,
               c.has_discount::text as has_discount, c.discount_amount, c.discount_percent
        from price_changes c join products p on p.id = c.product_id
        order by p.store, c.product_id, c.observed_at
    """,
}


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines from a .env file; blank lines and # comments skipped."""
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("'\"")
    return env


def export(conn: psycopg.Connection, out_dir: Path) -> dict[str, int]:
    """Write one CSV per query; returns data rows written per file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    with conn.cursor() as cur:
        for name, query in QUERIES.items():
            with (out_dir / name).open("wb") as f:
                f.write("﻿".encode())  # BOM, for Excel
                with cur.copy(f"copy ({query}) to stdout with (format csv, header true)") as copy:
                    for chunk in copy:
                        f.write(chunk)
            rows[name] = cur.rowcount
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "exports", help="output folder (default: exports/)")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL") or read_env_file(HERE / ".env").get("DATABASE_URL")
    if not url:
        ap.error("DATABASE_URL is not set and not found in .env")
    with psycopg.connect(url) as conn:
        rows = export(conn, args.out.expanduser())
    for name, n in rows.items():
        print(f"{args.out.expanduser() / name}: {n} rows")


if __name__ == "__main__":
    main()
