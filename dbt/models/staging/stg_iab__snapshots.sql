-- One row per city snapshot with the parameters of the Inside Airbnb occupancy model.
select
    city,
    city_name,
    country,
    snapshot_date,
    scrape_id,
    cast(average_nights_stay as double) as average_nights_stay,
    night_per_year_threshold,
    coalesce(str_threshold, 30) as str_threshold_nights,
    coalesce(requires_license, false) as requires_licence,
    currency_symbol,
    row_number() over (partition by city order by snapshot_date) as snapshot_seq,
    lag(snapshot_date) over (partition by city order by snapshot_date) as previous_snapshot_date,
    snapshot_date = max(snapshot_date) over (partition by city) as is_latest_snapshot
from {{ source('iab', 'snapshots') }}
