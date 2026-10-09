-- Grain: city x snapshot x room type (plus an 'All' row). ESTIMATES, not reported revenue.
--
-- Assumptions (Inside Airbnb occupancy model, see int_listing_snapshots):
--   * 50% of stays leave a review; nights per stay = max(minimum nights, city average stay);
--     occupancy is capped at 255 nights a year (~70%).
--   * Nightly price = `price` from listings.csv.gz. It equals price_quote_total_price divided by
--     the quoted nights for >99.8% of listings: a quote for one upcoming stay (mostly of
--     `minimum_nights`, starting a median 1-13 days after the scrape in these snapshots), so
--     length-of-stay discounts apply. Listings with no price are excluded from revenue (not
--     counted as 0).
--   * Prices are in local currency (EUR, USD, SGD); never sum across cities.
with f as (
    select * from {{ ref('fct_listing_snapshot') }}
),

expanded as (
    select room_type as room_type_group, f.* from f
    union all
    select 'All' as room_type_group, f.* from f
)

select
    city,
    snapshot_date,
    room_type_group as room_type,
    count(*) as listings,
    sum(case when has_price then 1 else 0 end) as priced_listings,
    avg(price) as mean_listed_price,
    {{ median('price') }} as median_listed_price,
    sum(case when has_price then est_nights_ltm else 0 end) as est_booked_nights_ltm,
    sum(est_revenue_ltm) as est_revenue_ltm,
    -- ADR: revenue-weighted average nightly rate of the modelled booked nights.
    {{ safe_divide('sum(est_revenue_ltm)', 'sum(case when has_price then est_nights_ltm else 0 end)') }} as est_adr,
    avg(est_revenue_ltm) as mean_est_revenue_per_priced_listing,
    {{ median('est_revenue_ltm') }} as median_est_revenue_per_priced_listing,
    -- RevPAL: estimated revenue per priced listing per calendar night.
    {{ safe_divide('sum(est_revenue_ltm)', 'sum(case when has_price then 365 else 0 end)') }} as est_revpal,
    sum(iab_estimated_revenue_l365d) as iab_published_revenue_l365d,
    sum(iab_estimated_occupancy_l365d) as iab_published_occupancy_nights_l365d
from expanded
group by city, snapshot_date, room_type_group
