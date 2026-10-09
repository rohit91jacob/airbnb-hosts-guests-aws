-- Grain: city x snapshot x rule. INDICATIVE flags, not legal determinations.
--
-- Rules live in seeds/city_regulation.csv (one row per city rule, with the official source):
--   est_nights_above       - in-scope listings whose modelled nights (12 months) exceed a cap
--   min_nights_below       - in-scope listings offering stays shorter than a legal minimum
--   unlicensed_short_term  - short-term listings (min stay < threshold) with no licence text
-- The licence classification itself (licensed / unlicensed / exempt / pending) follows the
-- Inside Airbnb dashboard exactly; it only checks whether *something* is in the field, not
-- whether a number is valid.
with f as (
    select * from {{ ref('fct_listing_snapshot') }}
),

rules as (
    select * from {{ ref('city_regulation') }}
),

evaluated as (
    select
        f.city,
        f.snapshot_date,
        r.rule_id,
        r.rule_type,
        r.threshold,
        r.description,
        r.source_url,
        case
            when r.room_type_scope = 'all' then true
            when r.room_type_scope = 'non_hotel' then f.room_type <> 'Hotel room'
            else f.room_type = r.room_type_scope
        end
        and not coalesce(
            r.exclude_licence_regex is not null
            and f.licence_text is not null
            and {{ regex_match('lower(f.licence_text)', 'r.exclude_licence_regex') }},
            false
        ) as in_scope,
        case
            when r.rule_type = 'est_nights_above' then f.est_nights_ltm > r.threshold
            when r.rule_type = 'min_nights_below' then f.minimum_nights < r.threshold
            when r.rule_type = 'unlicensed_short_term'
                then f.minimum_nights < r.threshold and f.licence_status = 'unlicensed'
            else false
        end as flagged
    from f
    inner join rules as r
        on r.city = f.city
),

rule_counts as (
    select
        city,
        snapshot_date,
        rule_id,
        min(rule_type) as rule_type,
        min(threshold) as threshold,
        min(description) as description,
        min(source_url) as source_url,
        sum(case when in_scope then 1 else 0 end) as listings_in_scope,
        sum(case when in_scope and flagged then 1 else 0 end) as listings_flagged
    from evaluated
    group by city, snapshot_date, rule_id
),

status as (
    select
        city,
        snapshot_date,
        count(*) as listings,
        sum(case when licence_status = 'licensed' then 1 else 0 end) as licensed,
        sum(case when licence_status = 'unlicensed' then 1 else 0 end) as unlicensed,
        sum(case when licence_status = 'exempt' then 1 else 0 end) as exempt,
        sum(case when licence_status = 'pending' then 1 else 0 end) as pending,
        sum(case when is_short_term and licence_status = 'unlicensed' then 1 else 0 end) as unlicensed_short_term
    from f
    group by city, snapshot_date
)

select
    s.city,
    s.snapshot_date,
    s.listings,
    s.licensed,
    s.unlicensed,
    s.exempt,
    s.pending,
    s.unlicensed_short_term,
    {{ safe_divide('s.unlicensed', 's.listings') }} as unlicensed_share,
    rc.rule_id,
    rc.rule_type,
    rc.threshold,
    rc.description as rule_description,
    rc.source_url as rule_source_url,
    rc.listings_in_scope,
    rc.listings_flagged,
    {{ safe_divide('rc.listings_flagged', 'rc.listings_in_scope') }} as flagged_share
from status as s
left join rule_counts as rc
    on
        rc.city = s.city
        and rc.snapshot_date = s.snapshot_date
