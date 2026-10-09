-- Listing counts survive every layer: bronze rows = 'all'-scope summary listings.
with bronze as (
    select city, snapshot_date, count(*) as n
    from {{ source('iab', 'listings') }}
    group by city, snapshot_date
),

mart as (
    select city, snapshot_date, listings as n
    from {{ ref('mart_city_snapshot_summary') }}
    where scope = 'all'
)

select b.city, b.snapshot_date, b.n as bronze_rows, m.n as mart_listings
from bronze as b
full outer join mart as m
    on
        m.city = b.city
        and m.snapshot_date = b.snapshot_date
where b.n is distinct from m.n
