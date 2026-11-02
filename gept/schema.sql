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

-- One row per product whenever price, old_price or in_stock changes
-- (the first observation counts as a change).
create table if not exists price_changes (
    id          bigserial primary key,
    product_id  bigint        not null references products (id),
    observed_at timestamptz   not null,
    price       numeric(12,2),
    old_price   numeric(12,2),
    in_stock    boolean       not null
);

create index if not exists price_changes_product_observed
    on price_changes (product_id, observed_at desc);
