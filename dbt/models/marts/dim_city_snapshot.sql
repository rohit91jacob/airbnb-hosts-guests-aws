-- Grain: city x snapshot. The snapshot calendar with the occupancy-model parameters each
-- snapshot was modelled with, materialised as a table so consumers outside dbt (the results
-- site, the DQ task on AWS) can read it from the catalog.
select
    city,
    city_name,
    country,
    snapshot_date,
    scrape_id,
    average_nights_stay,
    night_per_year_threshold,
    str_threshold_nights,
    requires_licence,
    currency_symbol,
    snapshot_seq,
    previous_snapshot_date,
    is_latest_snapshot
from {{ ref('stg_iab__snapshots') }}
