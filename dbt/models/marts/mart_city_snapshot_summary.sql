-- Grain: city x snapshot x scope.
--   all               every listing
--   short_term        minimum stay known and below the city's STR threshold (our definition)
--   dashboard_default the default filter of the Inside Airbnb city dashboards ("only short-term
--                     rentals"), reproduced with the dashboard's JavaScript conventions: an
--                     empty minimum_nights or price becomes 0 (+"" === 0 in JS), so listings
--                     with no minimum stay are counted as short-term there.
-- `dash_*` columns are whole-number averages exactly as the dashboard displays them. The
-- dashboard reads visualisations/listings.csv, where price is rounded to whole currency units,
-- so dash_avg_price and dash_avg_income use the rounded price. Every other revenue figure uses the exact
-- price from listings.csv.gz.
with f as (
    select * from {{ ref('fct_listing_snapshot') }}
),

scoped as (
    select 'all' as scope, f.* from f
    union all
    select 'short_term' as scope, f.* from f where f.is_short_term
    union all
    select 'dashboard_default' as scope, f.* from f
    where coalesce(f.minimum_nights, 0) < f.str_threshold_nights
),

agg as (
    select
        city,
        snapshot_date,
        scope,
        count(*) as listings,
        count(distinct host_key) as hosts,
        sum(case when room_type = 'Entire home/apt' then 1 else 0 end) as entire_homes,
        sum(case when room_type = 'Private room' then 1 else 0 end) as private_rooms,
        sum(case when room_type = 'Shared room' then 1 else 0 end) as shared_rooms,
        sum(case when room_type = 'Hotel room' then 1 else 0 end) as hotel_rooms,
        sum(case when has_price then 1 else 0 end) as priced_listings,
        avg(price) as mean_price,
        {{ median('price') }} as median_price,
        sum(case when is_short_term then 1 else 0 end) as short_term_listings,
        sum(case when is_multi_listing_host then 1 else 0 end) as multi_host_listings,
        sum(case when not is_multi_listing_host then 1 else 0 end) as single_host_listings,
        sum(case when is_highly_available then 1 else 0 end) as highly_available_listings,
        sum(case when licence_status = 'licensed' then 1 else 0 end) as licensed,
        sum(case when licence_status = 'unlicensed' then 1 else 0 end) as unlicensed,
        sum(case when licence_status = 'exempt' then 1 else 0 end) as exempt,
        sum(case when licence_status = 'pending' then 1 else 0 end) as pending,
        avg(est_nights_ltm) as mean_est_nights_ltm,
        avg(est_occupancy_rate) as mean_est_occupancy_rate,
        avg(est_revenue_ltm) as mean_est_revenue_ltm_priced,
        sum(est_revenue_ltm) as total_est_revenue_ltm,
        sum(case when has_price then est_nights_ltm else 0 end) as priced_est_nights_ltm,
        round(avg(est_nights_ltm)) as dash_avg_nights_booked,
        round(avg(coalesce(round(price), 0))) as dash_avg_price,
        -- the dashboard reads visualisations/listings.csv, whose price is rounded to whole units
        round(avg(coalesce(est_nights_ltm * round(price), 0))) as dash_avg_income
    from scoped
    group by city, snapshot_date, scope
)

select
    a.*,
    s.city_name,
    s.currency_symbol,
    s.average_nights_stay,
    s.str_threshold_nights,
    s.snapshot_seq,
    s.is_latest_snapshot,
    {{ safe_divide('a.entire_homes', 'a.listings') }} as entire_home_share,
    {{ safe_divide('a.private_rooms', 'a.listings') }} as private_room_share,
    {{ safe_divide('a.short_term_listings', 'a.listings') }} as short_term_share,
    {{ safe_divide('a.multi_host_listings', 'a.listings') }} as multi_host_listing_share,
    {{ safe_divide('a.highly_available_listings', 'a.listings') }} as highly_available_share,
    {{ safe_divide('a.unlicensed', 'a.listings') }} as unlicensed_share,
    {{ safe_divide('a.priced_listings', 'a.listings') }} as priced_share,
    -- Revenue-weighted average daily rate of the modelled booked nights.
    {{ safe_divide('a.total_est_revenue_ltm', 'a.priced_est_nights_ltm') }} as est_adr
from agg as a
inner join {{ ref('stg_iab__snapshots') }} as s
    on
        s.city = a.city
        and s.snapshot_date = a.snapshot_date
