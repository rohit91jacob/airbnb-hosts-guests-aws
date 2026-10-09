-- Guest sentiment, SIMPLE LEXICON METHOD (not a language model).
-- Counts words from a 138-word English list (seeds/sentiment_lexicon.csv) in each review.
-- Reviews in other languages, or with no lexicon word, are labelled 'no_signal'. There is no
-- negation handling ("not clean" counts as positive). Use it as a coarse trend indicator only.
with lex_pos as (
    select {{ array_agg_of('word') }} as words
    from {{ ref('sentiment_lexicon') }}
    where polarity = 'positive'
),

lex_neg as (
    select {{ array_agg_of('word') }} as words
    from {{ ref('sentiment_lexicon') }}
    where polarity = 'negative'
),

-- one row per review (a review can be attached to several sibling hotel listings)
distinct_reviews as (
    select * from (
        select
            r.*,
            row_number() over (partition by r.city, r.review_id order by r.listing_key) as rn
        from {{ ref('stg_iab__reviews') }} as r
    ) as d
    where rn = 1
),

tokenised as (
    select
        review_id,
        listing_key,
        city,
        review_date,
        {{ word_tokens('comments') }} as tokens
    from distinct_reviews
),

scored as (
    select
        t.review_id,
        t.listing_key,
        t.city,
        t.review_date,
        {{ dbt.date_trunc('month', 't.review_date') }} as review_month,
        {{ count_in('t.tokens', 'p.words') }} as positive_hits,
        {{ count_in('t.tokens', 'n.words') }} as negative_hits
    from tokenised as t
    cross join lex_pos as p
    cross join lex_neg as n
)

select
    *,
    case
        when positive_hits + negative_hits = 0 then 'no_signal'
        when positive_hits > negative_hits then 'positive'
        when negative_hits > positive_hits then 'negative'
        else 'mixed'
    end as sentiment_label,
    case
        when positive_hits + negative_hits > 0
            then {{ safe_divide('positive_hits - negative_hits', 'positive_hits + negative_hits') }}
    end as sentiment_score
from scored
