{#-
  SCD2 replay clock.

  dbt stamps dbt_valid_from / dbt_valid_to with snapshot_get_time(), normally "now". Inside
  Airbnb snapshots are historical, so `airbnb transform` replays them one round per snapshot
  date and passes that date as `as_of_round`; every version then starts (and ends) on the
  date the change was actually observed, and a rebuild from scratch is deterministic.
-#}
{% macro replay_clock() -%}
    {%- if var('as_of_round', none) -%}
        cast('{{ var("as_of_round") }} 00:00:00' as timestamp)
    {%- else -%}
        {{ current_timestamp() }}
    {%- endif -%}
{%- endmacro %}

{% macro duckdb__snapshot_get_time() -%}
    {{ replay_clock() }}
{%- endmacro %}

{% macro athena__snapshot_get_time() -%}
    {{ replay_clock() }}
{%- endmacro %}
