-- Every listing has at most one open SCD2 version, and versions never overlap in time.
with v as (
    select
        listing_key,
        dbt_valid_from,
        dbt_valid_to,
        lead(dbt_valid_from) over (partition by listing_key order by dbt_valid_from) as next_from
    from {{ ref('snap_listing_attributes') }}
)

select listing_key, 'more than one current version' as problem
from v
where dbt_valid_to is null
group by listing_key
having count(*) > 1

union all

select listing_key, 'overlapping or gapped versions' as problem
from v
where next_from is not null and (dbt_valid_to is null or dbt_valid_to <> next_from)
