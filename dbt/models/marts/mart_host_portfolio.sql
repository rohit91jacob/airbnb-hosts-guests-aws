-- Grain: city x snapshot. Single- vs multi-listing hosts, superhost share and host churn.
with h as (
    select * from {{ ref('int_hosts') }}
),

prev as (
    select
        s.city,
        s.snapshot_date,
        s.previous_snapshot_date
    from {{ ref('stg_iab__snapshots') }} as s
),

new_hosts as (
    select
        p.city,
        p.snapshot_date,
        count(*) as new_hosts
    from prev as p
    inner join h as cur
        on
            cur.city = p.city
            and cur.snapshot_date = p.snapshot_date
    left join h as old
        on
            old.host_key = cur.host_key
            and old.snapshot_date = p.previous_snapshot_date
    where p.previous_snapshot_date is not null and old.host_key is null
    group by p.city, p.snapshot_date
),

departed_hosts as (
    select
        p.city,
        p.snapshot_date,
        count(*) as departed_hosts
    from prev as p
    inner join h as old
        on
            old.city = p.city
            and old.snapshot_date = p.previous_snapshot_date
    left join h as cur
        on
            cur.host_key = old.host_key
            and cur.snapshot_date = p.snapshot_date
    where cur.host_key is null
    group by p.city, p.snapshot_date
),

churn as (
    select
        p.city,
        p.snapshot_date,
        coalesce(n.new_hosts, 0) as new_hosts,
        coalesce(d.departed_hosts, 0) as departed_hosts
    from prev as p
    left join new_hosts as n
        on
            n.city = p.city
            and n.snapshot_date = p.snapshot_date
    left join departed_hosts as d
        on
            d.city = p.city
            and d.snapshot_date = p.snapshot_date
    where p.previous_snapshot_date is not null
),

agg as (
    select
        city,
        snapshot_date,
        count(*) as hosts,
        sum(listings) as listings,
        sum(case when portfolio_tier = '1' then 1 else 0 end) as single_listing_hosts,
        sum(case when portfolio_tier <> '1' then 1 else 0 end) as multi_listing_hosts,
        sum(case when portfolio_tier = '10+' then 1 else 0 end) as hosts_10_plus,
        sum(case when portfolio_tier <> '1' then listings else 0 end) as listings_of_multi_listing_hosts,
        sum(case when portfolio_tier = '10+' then listings else 0 end) as listings_of_10_plus_hosts,
        sum(case when is_superhost then 1 else 0 end) as superhosts,
        sum(case when is_superhost then listings else 0 end) as listings_of_superhosts,
        max(listings) as largest_portfolio
    from h
    group by city, snapshot_date
)

select
    a.*,
    {{ safe_divide('a.single_listing_hosts', 'a.hosts') }} as single_listing_host_share,
    {{ safe_divide('a.listings_of_multi_listing_hosts', 'a.listings') }} as multi_listing_host_listing_share,
    {{ safe_divide('a.listings_of_10_plus_hosts', 'a.listings') }} as ten_plus_host_listing_share,
    {{ safe_divide('a.superhosts', 'a.hosts') }} as superhost_share_of_hosts,
    {{ safe_divide('a.listings_of_superhosts', 'a.listings') }} as superhost_share_of_listings,
    c.new_hosts,
    c.departed_hosts
from agg as a
left join churn as c
    on
        c.city = a.city
        and c.snapshot_date = a.snapshot_date
