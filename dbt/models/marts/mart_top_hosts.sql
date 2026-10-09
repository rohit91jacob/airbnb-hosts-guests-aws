-- Grain: city x snapshot x host, top 20 hosts by listing count in each snapshot.
with ranked as (
    select
        h.*,
        row_number() over (
            partition by h.city, h.snapshot_date
            order by h.listings desc, h.host_id
        ) as host_rank
    from {{ ref('int_hosts') }} as h
)

select
    city,
    snapshot_date,
    host_rank,
    host_key,
    host_id,
    listings,
    entire_homes,
    private_rooms,
    other_rooms,
    is_superhost,
    portfolio_tier
from ranked
where host_rank <= 20
