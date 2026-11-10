create table if not exists products (
    id               bigserial primary key,
    store            text        not null,
    store_product_id text        not null,
    name             text        not null,
    url              text        not null,
    category         text        not null,
    first_seen       timestamptz not null,
    last_seen        timestamptz not null,
    unique (store, store_product_id)
);

-- One row per product whenever price, old_price, in_stock or buyable changes
-- (the first observation counts as a change). The discount columns record
-- what the store reported at that moment; on their own they don't add a row.
create table if not exists price_changes (
    id               bigserial primary key,
    product_id       bigint        not null references products (id),
    observed_at      timestamptz   not null,
    price            numeric(12,2),
    old_price        numeric(12,2),
    in_stock         boolean,                 -- null: store doesn't report stock (zoommer)
    buyable          boolean       not null,  -- buy button enabled, not "coming soon", price > 0
    has_discount     boolean,
    discount_amount  numeric(12,2),
    discount_percent numeric(6,2)
);

create index if not exists price_changes_product_observed
    on price_changes (product_id, observed_at desc);

-- One row per store per saved run. price_changes only records changes, so this
-- is what tells "nothing changed" apart from "we didn't look" (gaps).
create table if not exists runs (
    store       text        not null,
    observed_at timestamptz not null,
    items       integer     not null,
    primary key (store, observed_at)
);
