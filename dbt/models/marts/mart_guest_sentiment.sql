-- Grain: city x review month. SIMPLE ENGLISH-LEXICON SENTIMENT (see int_review_sentiment):
-- word counting against a 138-word list, no negation handling, non-English reviews mostly
-- 'no_signal'. `lexicon_coverage` says how many reviews carried any signal at all.
select
    city,
    cast(review_month as date) as review_month,
    count(*) as reviews,
    sum(case when sentiment_label <> 'no_signal' then 1 else 0 end) as reviews_with_signal,
    {{ safe_divide("sum(case when sentiment_label <> 'no_signal' then 1 else 0 end)", 'count(*)') }} as lexicon_coverage,
    {{ safe_divide("sum(case when sentiment_label = 'positive' then 1 else 0 end)", "sum(case when sentiment_label <> 'no_signal' then 1 else 0 end)") }} as positive_share,
    {{ safe_divide("sum(case when sentiment_label = 'negative' then 1 else 0 end)", "sum(case when sentiment_label <> 'no_signal' then 1 else 0 end)") }} as negative_share,
    {{ safe_divide("sum(case when sentiment_label = 'mixed' then 1 else 0 end)", "sum(case when sentiment_label <> 'no_signal' then 1 else 0 end)") }} as mixed_share,
    avg(sentiment_score) as mean_sentiment_score,
    'simple English lexicon (seeds/sentiment_lexicon.csv); not a language model' as method
from {{ ref('int_review_sentiment') }}
group by city, cast(review_month as date)
