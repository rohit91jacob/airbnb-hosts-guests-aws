-- Listing x snapshot, enriched with the city parameters and the Inside Airbnb occupancy model.
--
-- Occupancy model (the "San Francisco model" as implemented by the v2 city dashboards):
--   estimated bookings (12 months) = number_of_reviews_ltm / review_rate (0.5)
--   nights per booking             = greatest(minimum_nights, city average length of stay)
--   estimated nights (12 months)   = round(least(bookings * nights per booking, 255))
--   estimated revenue (12 months)  = estimated nights * listed nightly price
-- The model uses the published number_of_reviews_ltm, as the dashboard does. Reviews are also
-- recounted from reviews.csv.gz as a cross-check; the recount is only complete for the
-- snapshot the reviews file came from (delisted listings' reviews vanish from later files).
with listings as (
    select * from {{ ref('stg_iab__listings') }}
),

snaps as (
    select * from {{ ref('stg_iab__snapshots') }}
),

reviews as (
    select listing_key, review_date, source_snapshot_date from {{ ref('stg_iab__reviews') }}
),

review_recount as (
    select
        l.listing_key,
        l.snapshot_date,
        count(*) as reviews_ltm_recount
    from listings as l
    inner join reviews as r
        on
            r.listing_key = l.listing_key
            and r.review_date > {{ dbt.dateadd('day', -365, 'l.last_scraped') }}
            and r.review_date <= l.last_scraped
    group by l.listing_key, l.snapshot_date
),

reviews_file as (
    select
        listing_key,
        max(source_snapshot_date) as source_snapshot_date
    from reviews
    group by listing_key
),

modelled as (
    select
        l.*,
        s.average_nights_stay,
        s.str_threshold_nights,
        s.night_per_year_threshold,
        s.requires_licence,
        s.currency_symbol,
        s.snapshot_seq,
        s.is_latest_snapshot,
        coalesce(rc.reviews_ltm_recount, 0) as reviews_ltm_recount,
        coalesce(rf.source_snapshot_date = l.snapshot_date, false) as reviews_file_is_this_snapshot,
        l.room_type = 'Entire home/apt' as is_entire_home,
        coalesce(l.minimum_nights < s.str_threshold_nights, false) as is_short_term,
        coalesce(l.calculated_host_listings_count, 1) > 1 as is_multi_listing_host,
        coalesce(l.availability_365, 0) > {{ var('highly_available_days') }} as is_highly_available,
        coalesce(l.last_review > {{ dbt.dateadd('month', -6, 'l.snapshot_date') }}, false) as is_recently_reviewed,
        l.price is not null as has_price,
        round(coalesce(l.number_of_reviews_ltm, 0) / {{ var('review_rate') }}) as est_bookings_ltm,
        -- coalesce: DuckDB's greatest() skips NULLs but Trino's returns NULL; the dashboard uses 0
        greatest(cast(coalesce(l.minimum_nights, 0) as double), s.average_nights_stay) as est_nights_per_booking
    from listings as l
    inner join snaps as s
        on
            s.city = l.city
            and s.snapshot_date = l.snapshot_date
    left join review_recount as rc
        on
            rc.listing_key = l.listing_key
            and rc.snapshot_date = l.snapshot_date
    left join reviews_file as rf
        on rf.listing_key = l.listing_key
),

nights as (
    select
        *,
        round(least(est_bookings_ltm * est_nights_per_booking, {{ var('max_nights_per_year') }})) as est_nights_ltm
    from modelled
)

select
    *,
    est_nights_ltm / 365.0 as est_occupancy_rate,
    round(est_nights_ltm * price) as est_revenue_ltm,
    est_nights_ltm > coalesce(night_per_year_threshold, 60) as is_frequently_booked
from nights
