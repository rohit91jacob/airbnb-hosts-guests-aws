-- Grain: listing. Current (or last known) attributes from the SCD2 history, plus lifecycle.
with versions as (
    select * from {{ ref('mart_price_change_history') }}
),

lifecycle as (
    select
        listing_key,
        min(valid_from) as first_seen,
        count(*) as versions,
        sum(case when change_type in ('price_up', 'price_down') then 1 else 0 end) as price_changes
    from versions
    group by listing_key
),

last_version as (
    select
        v.*,
        row_number() over (partition by v.listing_key order by v.valid_from desc) as rn
    from versions as v
)

select
    lv.listing_key,
    lv.city,
    lv.listing_id,
    lv.host_id,
    lv.room_type,
    lv.minimum_nights,
    lv.licence_text,
    lv.price as last_price,
    lc.first_seen,
    lv.valid_to as delisted_on,
    lv.is_current as is_active,
    lc.versions,
    lc.price_changes
from last_version as lv
inner join lifecycle as lc
    on lc.listing_key = lv.listing_key
where lv.rn = 1
