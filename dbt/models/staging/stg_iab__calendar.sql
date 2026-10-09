-- One row per listing per night. `available = false` means booked OR blocked by the host:
-- Airbnb's calendar does not distinguish them (Inside Airbnb data assumptions).
select
    city || ':' || cast(listing_id as varchar) as listing_key,
    city,
    snapshot_date,
    listing_id,
    calendar_date,
    available,
    minimum_nights,
    maximum_nights
from {{ source('iab', 'calendar') }}
