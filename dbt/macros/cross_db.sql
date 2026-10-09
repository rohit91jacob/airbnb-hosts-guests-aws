{#- Small portability layer so every model compiles for both DuckDB and Athena (Trino). -#}

{#- "$1,234.00" / "€95.00" -> 1234.0; NULL when nothing numeric is left. -#}
{% macro parse_price(expr) -%}
    {{ return(adapter.dispatch('parse_price')(expr)) }}
{%- endmacro %}
{% macro default__parse_price(expr) -%}
    try_cast(nullif(regexp_replace({{ expr }}, '[^0-9.]', '', 'g'), '') as double)
{%- endmacro %}
{% macro athena__parse_price(expr) -%}
    try_cast(nullif(regexp_replace({{ expr }}, '[^0-9.]', ''), '') as double)
{%- endmacro %}

{% macro regex_match(expr, pattern) -%}
    {{ return(adapter.dispatch('regex_match')(expr, pattern)) }}
{%- endmacro %}
{% macro default__regex_match(expr, pattern) -%}
    regexp_matches({{ expr }}, {{ pattern }})
{%- endmacro %}
{% macro athena__regex_match(expr, pattern) -%}
    regexp_like({{ expr }}, {{ pattern }})
{%- endmacro %}

{% macro median(expr) -%}
    {{ return(adapter.dispatch('median')(expr)) }}
{%- endmacro %}
{% macro default__median(expr) -%}
    median({{ expr }})
{%- endmacro %}
{% macro athena__median(expr) -%}
    approx_percentile({{ expr }}, 0.5)
{%- endmacro %}

{% macro safe_divide(num, den) -%}
    cast({{ num }} as double) / nullif(cast({{ den }} as double), 0)
{%- endmacro %}

{#- Lower-cased word tokens (ASCII letters and apostrophes) as an array. -#}
{% macro word_tokens(expr) -%}
    {{ return(adapter.dispatch('word_tokens')(expr)) }}
{%- endmacro %}
{% macro default__word_tokens(expr) -%}
    regexp_split_to_array(lower(coalesce({{ expr }}, '')), '[^a-z'']+')
{%- endmacro %}
{% macro athena__word_tokens(expr) -%}
    regexp_split(lower(coalesce({{ expr }}, '')), '[^a-z'']+')
{%- endmacro %}

{% macro array_agg_of(expr) -%}
    {{ return(adapter.dispatch('array_agg_of')(expr)) }}
{%- endmacro %}
{% macro default__array_agg_of(expr) -%}
    list({{ expr }})
{%- endmacro %}
{% macro athena__array_agg_of(expr) -%}
    array_agg({{ expr }})
{%- endmacro %}

{#- Number of elements of `tokens` that appear in the array `lexicon`. -#}
{% macro count_in(tokens, lexicon) -%}
    {{ return(adapter.dispatch('count_in')(tokens, lexicon)) }}
{%- endmacro %}
{% macro default__count_in(tokens, lexicon) -%}
    len(list_filter({{ tokens }}, t -> list_contains({{ lexicon }}, t)))
{%- endmacro %}
{% macro athena__count_in(tokens, lexicon) -%}
    cardinality(filter({{ tokens }}, t -> contains({{ lexicon }}, t)))
{%- endmacro %}

{#- Strip the HTML line breaks Inside Airbnb leaves in review text. -#}
{% macro clean_review_text(expr) -%}
    {{ return(adapter.dispatch('clean_review_text')(expr)) }}
{%- endmacro %}
{% macro default__clean_review_text(expr) -%}
    trim(regexp_replace({{ expr }}, '<br\s*/?>|\r|\n', ' ', 'g'))
{%- endmacro %}
{% macro athena__clean_review_text(expr) -%}
    trim(regexp_replace({{ expr }}, '<br\s*/?>|\r|\n', ' '))
{%- endmacro %}

{#-
  Licence status exactly as the Inside Airbnb dashboards classify it (map.js, v2.2):
  empty -> unlicensed; /exempt|bail mobilité|32+ days listing/i -> exempt;
  /pending/i -> pending; anything else -> licensed.
-#}
{% macro licence_status(expr) -%}
    case
        when {{ expr }} is null or trim({{ expr }}) = '' then 'unlicensed'
        when {{ regex_match('lower(' ~ expr ~ ')', "'exempt|bail mobilité|32\+ days listing'") }} then 'exempt'
        when {{ regex_match('lower(' ~ expr ~ ')', "'pending'") }} then 'pending'
        else 'licensed'
    end
{%- endmacro %}
