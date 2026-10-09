-- Guest reviews. Reviewer names are not loaded into bronze at all.
select
    review_id,
    city || ':' || cast(listing_id as varchar) as listing_key,
    city,
    listing_id,
    review_date,
    reviewer_id,
    {{ clean_review_text('comments') }} as comments,
    snapshot_date as source_snapshot_date
from {{ source('iab', 'reviews') }}
where review_id is not null
