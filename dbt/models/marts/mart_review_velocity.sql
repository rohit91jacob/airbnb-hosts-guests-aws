-- Grain: city x review month. Reviews written per month and per reviewed listing, with the
-- same month a year earlier for a year-over-year comparison.
-- Source: the newest reviews.csv.gz per city. Reviews of listings delisted before that
-- snapshot are not in the file, so older months are undercounted (survivorship); the most
-- recent month is partial (it ends on the scrape date).
with r as (
    select * from {{ ref('stg_iab__reviews') }}
),

latest as (
    select city, max(snapshot_date) as snapshot_date
    from {{ ref('stg_iab__snapshots') }}
    group by city
),

monthly as (
    select
        city,
        {{ dbt.date_trunc('month', 'review_date') }} as review_month,
        count(distinct review_id) as reviews,
        count(distinct listing_key) as listings_reviewed,
        count(distinct reviewer_id) as distinct_reviewers
    from r
    group by city, {{ dbt.date_trunc('month', 'review_date') }}
)

select
    m.city,
    cast(m.review_month as date) as review_month,
    m.reviews,
    m.listings_reviewed,
    m.distinct_reviewers,
    {{ safe_divide('m.reviews', 'm.listings_reviewed') }} as reviews_per_reviewed_listing,
    py.reviews as reviews_same_month_last_year,
    {{ safe_divide('m.reviews - py.reviews', 'py.reviews') }} as reviews_yoy_change,
    cast(m.review_month as date) = cast({{ dbt.date_trunc('month', 'l.snapshot_date') }} as date) as is_partial_month
from monthly as m
inner join latest as l
    on l.city = m.city
left join monthly as py
    on
        py.city = m.city
        and py.review_month = {{ dbt.dateadd('year', -1, 'm.review_month') }}
