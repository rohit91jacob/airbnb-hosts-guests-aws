{#-
  DuckDB: custom schemas are used as-is (staging, intermediate, marts, snapshots, reference).
  Athena: each maps to its own Glue database, prefixed to match Terraform
  (e.g. airbnb_marts), because a Glue database is the Athena equivalent of a schema.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- elif target.type == 'athena' -%}
        {{ target.schema }}_{{ custom_schema_name | trim }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
