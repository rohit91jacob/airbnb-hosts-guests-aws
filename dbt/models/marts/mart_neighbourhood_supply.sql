-- Grain: city x snapshot x neighbourhood (Inside Airbnb's cleansed neighbourhood, derived from
-- coordinates and the city's own boundaries). Supply mix and the change since the previous
-- snapshot of the same city.
with f as (
    select * from {{ ref('fct_listing_snapshot') }}
),

agg as (
    select
        city,
        snapshot_date,
        coalesce(neighbourhood, '(unknown)') as neighbourhood,
        min(neighbourhood_group) as neighbourhood_group,
        count(*) as listings,
        sum(case when is_entire_home then 1 else 0 end) as entire_homes,
        sum(case when is_short_term then 1 else 0 end) as short_term_listings,
        sum(case when is_multi_listing_host then 1 else 0 end) as multi_host_listings,
        sum(case when is_highly_available then 1 else 0 end) as highly_available_listings,
        count(distinct host_key) as hosts,
        {{ median('price') }} as median_price,
        avg(est_occupancy_rate) as mean_est_occupancy_rate,
        sum(est_revenue_ltm) as est_revenue_ltm
    from f
    group by city, snapshot_date, coalesce(neighbourhood, '(unknown)')
),

with_prev as (
    select
        a.*,
        s.previous_snapshot_date,
        sum(a.listings) over (partition by a.city, a.snapshot_date) as city_listings
    from agg as a
    inner join {{ ref('stg_iab__snapshots') }} as s
        on
            s.city = a.city
            and s.snapshot_date = a.snapshot_date
)

select
    c.city,
    c.snapshot_date,
    c.neighbourhood,
    c.neighbourhood_group,
    c.listings,
    c.hosts,
    c.entire_homes,
    c.short_term_listings,
    c.multi_host_listings,
    c.highly_available_listings,
    c.median_price,
    c.mean_est_occupancy_rate,
    c.est_revenue_ltm,
    {{ safe_divide('c.listings', 'c.city_listings') }} as share_of_city_listings,
    {{ safe_divide('c.entire_homes', 'c.listings') }} as entire_home_share,
    {{ safe_divide('c.short_term_listings', 'c.listings') }} as short_term_share,
    {{ safe_divide('c.multi_host_listings', 'c.listings') }} as multi_host_listing_share,
    p.listings as previous_listings,
    c.listings - p.listings as listings_change,
    {{ safe_divide('c.listings - p.listings', 'p.listings') }} as listings_change_pct
from with_prev as c
left join agg as p
    on
        p.city = c.city
        and p.neighbourhood = c.neighbourhood
        and p.snapshot_date = c.previous_snapshot_date
