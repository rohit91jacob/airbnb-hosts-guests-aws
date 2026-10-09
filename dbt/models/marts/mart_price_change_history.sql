-- Grain: one SCD2 version of a listing (from snap_listing_attributes).
-- Each version starts on the snapshot date a change was first observed. change_type says what
-- changed relative to the previous version of the same listing.
with v as (
    select
        s.*,
        row_number() over (partition by s.listing_key order by s.dbt_valid_from) as version_number,
        lag(s.price) over (partition by s.listing_key order by s.dbt_valid_from) as previous_price,
        lag(s.room_type) over (partition by s.listing_key order by s.dbt_valid_from) as previous_room_type,
        lag(s.minimum_nights) over (partition by s.listing_key order by s.dbt_valid_from) as previous_minimum_nights,
        lag(s.licence_text) over (partition by s.listing_key order by s.dbt_valid_from) as previous_licence_text,
        lag(s.host_id) over (partition by s.listing_key order by s.dbt_valid_from) as previous_host_id,
        lead(s.dbt_valid_from) over (partition by s.listing_key order by s.dbt_valid_from) as next_valid_from
    from {{ ref('snap_listing_attributes') }} as s
)

select
    listing_key,
    city,
    listing_id,
    version_number,
    cast(dbt_valid_from as date) as valid_from,
    cast(dbt_valid_to as date) as valid_to,
    dbt_valid_to is null as is_current,
    dbt_valid_to is not null and next_valid_from is null as delisted_at_valid_to,
    host_id,
    room_type,
    minimum_nights,
    licence_text,
    price,
    previous_price,
    price - previous_price as price_change,
    {{ safe_divide('price - previous_price', 'previous_price') }} as price_change_pct,
    version_number > 1 and room_type is distinct from previous_room_type as room_type_changed,
    version_number > 1 and minimum_nights is distinct from previous_minimum_nights as minimum_nights_changed,
    version_number > 1 and licence_text is distinct from previous_licence_text as licence_changed,
    version_number > 1 and host_id is distinct from previous_host_id as host_changed,
    case
        when version_number = 1 then 'first_seen'
        when price is not null and previous_price is null then 'price_added'
        when price is null and previous_price is not null then 'price_removed'
        when price > previous_price then 'price_up'
        when price < previous_price then 'price_down'
        else 'attributes_only'
    end as change_type
from v
