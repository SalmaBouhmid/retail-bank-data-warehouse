{#- Use the schema name as written (staging, reference...) instead of dbt's default
    "public_staging": the warehouse schemas keep simple names. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name is not none else target.schema }}
{%- endmacro %}
