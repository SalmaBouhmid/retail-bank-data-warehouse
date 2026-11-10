-- Fact table: one row per account and per month (Q2: average balance, monthly activity).
select
    monthly.account_id,
    accounts.owner_client_id,
    accounts.district_id,
    monthly.month_start,
    monthly.n_transactions,
    monthly.credits,
    monthly.debits,
    monthly.net_movement,
    monthly.month_end_balance,
    monthly.min_balance,
    monthly.had_negative_balance
from {{ ref('int_account_monthly_balances') }} as monthly
inner join {{ ref('dim_account') }} as accounts on accounts.account_id = monthly.account_id
