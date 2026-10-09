-- Forward-looking availability from calendar.csv.gz, per listing x snapshot.
-- "Unavailable" nights are booked OR blocked by the host: the calendar cannot tell them apart,
-- so unavailability is an upper bound on bookings, not a booking count.
with cal as (
    select * from {{ ref('stg_iab__calendar') }}
),

bounds as (
    select
        listing_key,
        snapshot_date,
        min(calendar_date) as first_night
    from cal
    group by listing_key, snapshot_date
)

select
    cal.listing_key,
    cal.snapshot_date,
    min(cal.city) as city,
    min(b.first_night) as first_night,
    max(cal.calendar_date) as last_night,
    count(*) as calendar_nights,
    sum(case when cal.available then 1 else 0 end) as available_nights_365,
    sum(case when not cal.available then 1 else 0 end) as unavailable_nights_365,
    sum(case when cal.available and cal.calendar_date < {{ dbt.dateadd('day', 30, 'b.first_night') }} then 1 else 0 end) as available_nights_30,
    sum(case when cal.available and cal.calendar_date < {{ dbt.dateadd('day', 90, 'b.first_night') }} then 1 else 0 end) as available_nights_90,
    sum(case when not cal.available and cal.calendar_date < {{ dbt.dateadd('day', 30, 'b.first_night') }} then 1 else 0 end) as unavailable_nights_30,
    sum(case when not cal.available and cal.calendar_date < {{ dbt.dateadd('day', 90, 'b.first_night') }} then 1 else 0 end) as unavailable_nights_90
from cal
inner join bounds as b
    on
        b.listing_key = cal.listing_key
        and b.snapshot_date = cal.snapshot_date
group by cal.listing_key, cal.snapshot_date
