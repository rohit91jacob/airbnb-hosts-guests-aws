{#-
  SCD Type 2 history of listing price and attributes across Inside Airbnb snapshots.

  `airbnb transform` replays snapshot dates in order ("rounds"). In each round the source
  below is, for every city, its latest snapshot on or before `as_of_round`. dbt then closes
  versions whose tracked columns changed, opens new ones and (hard_deletes='invalidate')
  closes listings that are no longer listed. snapshot_get_time() is overridden to return the
  round date (macros/snapshot_time.sql), so dbt_valid_from is the date a change was first
  observed, not the date the pipeline happened to run, and a rebuild is deterministic.

  bedrooms/beds/bathrooms are carried but not tracked: their scrape coverage varies a lot
  between snapshots (e.g. beds is null for 58% of Singapore listings in June 2026 and 5% in
  September), so tracking them would mint versions for fill-ins rather than real changes.
-#}
{% snapshot snap_listing_attributes %}
{{
    config(
        unique_key='listing_key',
        strategy='check',
        check_cols=[
            'price', 'room_type', 'property_type', 'accommodates',
            'minimum_nights', 'maximum_nights', 'licence_text', 'host_id',
            'host_is_superhost', 'instant_bookable', 'neighbourhood'
        ],
        hard_deletes='invalidate',
    )
}}

with source_rows as (
    select * from {{ ref('int_listing_snapshots') }}
),

round_snapshot as (
    select
        city,
        max(snapshot_date) as snapshot_date
    from source_rows
    {% if var('as_of_round', none) %}
        where snapshot_date <= cast('{{ var("as_of_round") }}' as date)
    {% endif %}
    group by city
)

select
    s.listing_key,
    s.city,
    s.listing_id,
    s.host_id,
    s.room_type,
    s.property_type,
    s.accommodates,
    s.bedrooms,
    s.beds,
    s.price,
    s.minimum_nights,
    s.maximum_nights,
    s.licence_text,
    s.host_is_superhost,
    s.instant_bookable,
    s.neighbourhood,
    s.snapshot_date as observed_in_snapshot
from source_rows as s
inner join round_snapshot as r
    on
        r.city = s.city
        and r.snapshot_date = s.snapshot_date

{% endsnapshot %}
