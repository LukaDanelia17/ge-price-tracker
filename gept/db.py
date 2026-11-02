"""Postgres storage: upsert products, insert price_changes only on change."""

from datetime import datetime
from importlib.resources import files

import psycopg

from .parse import Item


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url)


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(files("gept").joinpath("schema.sql").read_text())
    conn.commit()


def changed_items(latest: dict[str, tuple], items: list[Item]) -> list[Item]:
    """Items whose (price, old_price, in_stock) differs from the latest stored
    state, keyed by store_product_id. New products always count as changed."""
    return [i for i in items if latest.get(i.store_product_id) != i.state()]


def save(conn: psycopg.Connection, store: str, items: list[Item], observed_at: datetime) -> int:
    """Save one store's snapshot in a single transaction. Returns rows added to price_changes."""
    if not items:
        return 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into products (store, store_product_id, name, url, category, first_seen, last_seen)
            select %(store)s, t.pid, t.name, t.url, t.category, %(ts)s, %(ts)s
            from unnest(%(pids)s::text[], %(names)s::text[], %(urls)s::text[], %(cats)s::text[])
                 as t(pid, name, url, category)
            on conflict (store, store_product_id) do update
                set name = excluded.name, url = excluded.url, last_seen = excluded.last_seen
            returning store_product_id, id
            """,
            {
                "store": store,
                "ts": observed_at,
                "pids": [i.store_product_id for i in items],
                "names": [i.name for i in items],
                "urls": [i.url for i in items],
                "cats": [i.category for i in items],
            },
        )
        ids = dict(cur.fetchall())

        cur.execute(
            """
            select distinct on (p.store_product_id) p.store_product_id, c.price, c.old_price, c.in_stock
            from price_changes c join products p on p.id = c.product_id
            where c.product_id = any(%s)
            order by p.store_product_id, c.observed_at desc, c.id desc
            """,
            (list(ids.values()),),
        )
        latest = {pid: (price, old_price, in_stock) for pid, price, old_price, in_stock in cur.fetchall()}

        changes = changed_items(latest, items)
        cur.executemany(
            "insert into price_changes (product_id, observed_at, price, old_price, in_stock) values (%s, %s, %s, %s, %s)",
            [(ids[i.store_product_id], observed_at, i.price, i.old_price, i.in_stock) for i in changes],
        )
    return len(changes)
