-- Grain: city x snapshot (from the second snapshot of each city on). What changed since the
-- city's previous snapshot, read from the SCD2 history.
with snaps as (
    select * from {{ ref('stg_iab__snapshots') }}
    where previous_snapshot_date is not null
),

h as (
    select * from {{ ref('mart_price_change_history') }}
),

changes as (
    select
        s.city,
        s.snapshot_date,
        s.previous_snapshot_date,
        sum(case when h.change_type = 'first_seen' then 1 else 0 end) as new_listings,
        sum(case when h.change_type = 'price_up' then 1 else 0 end) as price_increases,
        sum(case when h.change_type = 'price_down' then 1 else 0 end) as price_decreases,
        sum(case when h.change_type = 'price_added' then 1 else 0 end) as prices_added,
        sum(case when h.change_type = 'price_removed' then 1 else 0 end) as prices_removed,
        sum(case when h.change_type = 'attributes_only' then 1 else 0 end) as attribute_only_changes,
        sum(case when h.minimum_nights_changed then 1 else 0 end) as minimum_nights_changes,
        sum(case when h.licence_changed then 1 else 0 end) as licence_changes,
        sum(case when h.room_type_changed then 1 else 0 end) as room_type_changes,
        {{ median("case when h.change_type in ('price_up', 'price_down') then h.price_change_pct end") }} as median_price_change_pct,
        avg(case when h.change_type in ('price_up', 'price_down') then h.price_change_pct end) as mean_price_change_pct
    from snaps as s
    left join h
        on
            h.city = s.city
            and h.valid_from = s.snapshot_date
    group by s.city, s.snapshot_date, s.previous_snapshot_date
),

delisted as (
    select
        s.city,
        s.snapshot_date,
        count(h.listing_key) as delisted_listings
    from snaps as s
    left join h
        on
            h.city = s.city
            and h.valid_to = s.snapshot_date
            and h.delisted_at_valid_to
    group by s.city, s.snapshot_date
),

both_priced as (
    select
        cur.city,
        cur.snapshot_date,
        count(*) as listings_in_both,
        sum(case when cur.has_price and prev.has_price then 1 else 0 end) as priced_in_both
    from {{ ref('fct_listing_snapshot') }} as cur
    inner join snaps as s
        on
            s.city = cur.city
            and s.snapshot_date = cur.snapshot_date
    inner join {{ ref('fct_listing_snapshot') }} as prev
        on
            prev.listing_key = cur.listing_key
            and prev.snapshot_date = s.previous_snapshot_date
    group by cur.city, cur.snapshot_date
)

select
    c.*,
    d.delisted_listings,
    b.listings_in_both,
    b.priced_in_both,
    {{ safe_divide('c.price_increases + c.price_decreases', 'b.priced_in_both') }} as price_changed_share
from changes as c
left join delisted as d
    on
        d.city = c.city
        and d.snapshot_date = c.snapshot_date
left join both_priced as b
    on
        b.city = c.city
        and b.snapshot_date = c.snapshot_date
