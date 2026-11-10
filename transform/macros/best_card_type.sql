{#- Aggregate: the best card type of a group of cards (gold > classic > junior). -#}
{% macro best_card_type(card_type_column) -%}
    case max(case {{ card_type_column }} when 'gold' then 3 when 'classic' then 2 when 'junior' then 1 end)
        when 3 then 'gold'
        when 2 then 'classic'
        when 1 then 'junior'
    end
{%- endmacro %}
