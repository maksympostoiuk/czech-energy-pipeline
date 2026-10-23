{#
    Use the configured schema as is (staging, intermediate, marts, reference)
    instead of dbt's default "<target_schema>_<custom_schema>" prefixing.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
