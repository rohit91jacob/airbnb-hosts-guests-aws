-- The SCD2 history must agree with a direct comparison of consecutive snapshots: for every
-- city and snapshot, the number of listings whose price changed since the previous snapshot
-- equals the number of SCD2 versions opened that day with a price change.
with snaps as (
    select city, snapshot_date, previous_snapshot_date
    from {{ ref('stg_iab__snapshots') }}
    where previous_snapshot_date is not null
),

direct as (
    select
        s.city,
        s.snapshot_date,
        sum(case when cur.price is distinct from prev.price then 1 else 0 end) as price_changes
    from snaps as s
    inner join {{ ref('int_listing_snapshots') }} as cur
        on
            cur.city = s.city
            and cur.snapshot_date = s.snapshot_date
    inner join {{ ref('int_listing_snapshots') }} as prev
        on
            prev.listing_key = cur.listing_key
            and prev.snapshot_date = s.previous_snapshot_date
    group by s.city, s.snapshot_date
),

scd2 as (
    select
        s.city,
        s.snapshot_date,
        sum(
            case
                when h.change_type in ('price_up', 'price_down', 'price_added', 'price_removed')
                    then 1
                else 0
            end
        ) as price_changes
    from snaps as s
    inner join {{ ref('mart_price_change_history') }} as h
        on
            h.city = s.city
            and h.valid_from = s.snapshot_date
    -- only listings that were also listed in the previous snapshot (a relisted listing
    -- opens a new version too, but the direct pair comparison cannot see it)
    inner join {{ ref('int_listing_snapshots') }} as prev
        on
            prev.listing_key = h.listing_key
            and prev.snapshot_date = s.previous_snapshot_date
    group by s.city, s.snapshot_date
)

select d.city, d.snapshot_date, d.price_changes as direct_changes, c.price_changes as scd2_changes
from direct as d
left join scd2 as c
    on
        c.city = d.city
        and c.snapshot_date = d.snapshot_date
where d.price_changes <> coalesce(c.price_changes, 0)
