-- Host x snapshot portfolio within a city (a host id can list in several cities).
select
    host_key,
    city,
    snapshot_date,
    min(host_id) as host_id,
    max(host_name) as host_name,
    min(host_since) as host_since,
    count(*) as listings,
    sum(case when room_type = 'Entire home/apt' then 1 else 0 end) as entire_homes,
    sum(case when room_type = 'Private room' then 1 else 0 end) as private_rooms,
    sum(case when room_type in ('Shared room', 'Hotel room') then 1 else 0 end) as other_rooms,
    max(case when host_is_superhost then 1 else 0 end) = 1 as is_superhost,
    max(calculated_host_listings_count) as calculated_host_listings_count,
    case
        when count(*) = 1 then '1'
        when count(*) <= 4 then '2-4'
        when count(*) <= 9 then '5-9'
        else '10+'
    end as portfolio_tier
from {{ ref('stg_iab__listings') }}
group by host_key, city, snapshot_date
