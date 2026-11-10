{{ config(materialized='table') }}
-- One row per account and per month, from the month of its first transaction to the end
-- of the data, INCLUDING months without any transaction: their balance is the balance
-- carried over from the last month with activity (otherwise average balances would be wrong).
with daily as (
    select * from {{ ref('int_daily_balances') }}
),

months as (
    select month_start::date as month_start
    from generate_series(
        '{{ var("first_date") }}'::date, '{{ var("reference_date") }}'::date, interval '1 month'
    ) as month_start
),

account_months as (
    select first_days.account_id, months.month_start
    from (
        select account_id, min(balance_date) as first_day from daily group by account_id
    ) as first_days
    inner join months on months.month_start >= date_trunc('month', first_days.first_day)
),

activity as (
    select
        account_id,
        date_trunc('month', balance_date)::date as month_start,
        sum(n_transactions) as n_transactions,
        sum(credits) as credits,
        sum(debits) as debits,
        min(end_of_day_balance) as min_end_of_day_balance,
        (array_agg(end_of_day_balance order by balance_date desc))[1] as last_balance_of_month
    from daily
    group by account_id, date_trunc('month', balance_date)
),

joined as (
    select
        account_months.account_id,
        account_months.month_start,
        activity.n_transactions,
        activity.credits,
        activity.debits,
        activity.min_end_of_day_balance,
        activity.last_balance_of_month,
        -- months grouped with the last month that had activity (to carry its balance over)
        count(activity.last_balance_of_month) over (
            partition by account_months.account_id order by account_months.month_start
        ) as activity_group
    from account_months
    left join activity
        on activity.account_id = account_months.account_id
        and activity.month_start = account_months.month_start
),

carried as (
    select
        *,
        first_value(last_balance_of_month) over (
            partition by account_id, activity_group order by month_start
        ) as month_end_balance
    from joined
)

select
    account_id,
    month_start,
    coalesce(n_transactions, 0) as n_transactions,
    coalesce(credits, 0) as credits,
    coalesce(debits, 0) as debits,
    coalesce(credits, 0) - coalesce(debits, 0) as net_movement,
    month_end_balance,
    coalesce(min_end_of_day_balance, month_end_balance) as min_balance,
    coalesce(min_end_of_day_balance, month_end_balance) < 0 as had_negative_balance
from carried
