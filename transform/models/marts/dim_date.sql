-- Calendar: one row per day from the first to the last day of the data.
select
    day::date as date_day,
    extract(year from day)::int as year,
    extract(quarter from day)::int as quarter,
    extract(month from day)::int as month,
    to_char(day, 'FMMonth') as month_name,
    to_char(day, 'YYYY-MM') as year_month,
    date_trunc('month', day)::date as month_start,
    extract(isodow from day)::int as day_of_week,  -- 1 = Monday ... 7 = Sunday
    to_char(day, 'FMDay') as day_name,
    extract(isodow from day) in (6, 7) as is_weekend
from generate_series(
    '{{ var("first_date") }}'::date, '{{ var("reference_date") }}'::date, interval '1 day'
) as day
