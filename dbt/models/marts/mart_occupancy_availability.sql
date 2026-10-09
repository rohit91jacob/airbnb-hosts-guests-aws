-- Grain: city x snapshot x room type (plus an 'All' row).
-- Two independent lenses on how busy listings are:
--   * calendar: share of the next 30 nights not available (booked OR blocked: an upper bound);
--   * reviews:  the Inside Airbnb occupancy model (est_occupancy_rate, capped at 255/365).
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
    avg(available_nights_30) as mean_available_nights_30,
    avg(available_nights_90) as mean_available_nights_90,
    avg(available_nights_365) as mean_available_nights_365,
    avg(unavailable_share_30) as mean_unavailable_share_30,
    {{ safe_divide('sum(case when is_highly_available then 1 else 0 end)', 'count(*)') }} as highly_available_share,
    {{ safe_divide('sum(case when available_nights_365 = 0 then 1 else 0 end)', 'count(*)') }} as fully_blocked_share,
    avg(est_occupancy_rate) as mean_est_occupancy_rate,
    {{ median('est_occupancy_rate') }} as median_est_occupancy_rate,
    avg(est_nights_ltm) as mean_est_nights_ltm,
    {{ safe_divide('sum(case when is_frequently_booked then 1 else 0 end)', 'count(*)') }} as frequently_booked_share,
    {{ safe_divide('sum(case when is_recently_reviewed then 1 else 0 end)', 'count(*)') }} as recently_reviewed_share
from expanded
group by city, snapshot_date, room_type_group
