{#- Generic tests used across the project (no external packages required). -#}

{% test between(model, column_name, min_value=none, max_value=none) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and (
    {% if min_value is not none %}{{ column_name }} < {{ min_value }}{% else %}false{% endif %}
    or {% if max_value is not none %}{{ column_name }} > {{ max_value }}{% else %}false{% endif %}
  )
{% endtest %}

{% test unique_combination(model, combination) %}
select {{ combination | join(', ') }}, count(*) as n
from {{ model }}
group by {{ combination | join(', ') }}
having count(*) > 1
{% endtest %}

{% test row_count_at_least(model, min_rows) %}
select n from (select count(*) as n from {{ model }}) as c
where n < {{ min_rows }}
{% endtest %}
