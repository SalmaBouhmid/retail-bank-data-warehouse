-- For every month with activity, the month-end balance must equal the end-of-day balance
-- of the last active day of that month. Returns the months that differ (expect 0 rows).
with last_days as (
    select distinct on (account_id, date_trunc('month', balance_date))
        account_id,
        date_trunc('month', balance_date)::date as month_start,
        end_of_day_balance
    from {{ ref('int_daily_balances') }}
    order by account_id, date_trunc('month', balance_date), balance_date desc
)

select monthly.account_id, monthly.month_start, monthly.month_end_balance, last_days.end_of_day_balance
from {{ ref('fct_monthly_balance') }} as monthly
inner join last_days
    on last_days.account_id = monthly.account_id
    and last_days.month_start = monthly.month_start
where monthly.month_end_balance <> last_days.end_of_day_balance
