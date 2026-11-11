-- Fact table: one row per loan (Q3: problem loans, Q4: indicators known before the loan).
select
    loans.loan_id,
    loans.account_id,
    accounts.owner_client_id,
    accounts.district_id,
    loans.granted_on,
    loans.amount,
    -- amount bands for the report (the order column lets Power BI sort them correctly)
    case
        when loans.amount < 50000 then '< 50k'
        when loans.amount < 100000 then '50k-100k'
        when loans.amount < 200000 then '100k-200k'
        when loans.amount < 300000 then '200k-300k'
        else '300k+'
    end as amount_band,
    case
        when loans.amount < 50000 then 1
        when loans.amount < 100000 then 2
        when loans.amount < 200000 then 3
        when loans.amount < 300000 then 4
        else 5
    end as amount_band_order,
    loans.duration_months,
    loans.monthly_payment,
    loans.status_code,
    loans.loan_status,
    loans.is_default,
    loans.is_finished,
    case when loans.is_default then loans.amount else 0 end as amount_in_default,
    features.client_age_at_loan,
    features.had_card_before_loan,
    features.n_transactions_before,
    features.days_of_history_before,
    features.n_penalty_interest_before,
    features.n_cash_withdrawals_before,
    features.received_pension_before,
    features.min_balance_before,
    features.n_days_negative_balance_before,
    features.balance_before_loan,
    features.n_full_months_before,
    features.avg_month_end_balance_before,
    features.avg_monthly_credits_before,
    features.avg_monthly_debits_before
from {{ ref('stg_loans') }} as loans
inner join {{ ref('dim_account') }} as accounts on accounts.account_id = loans.account_id
inner join {{ ref('int_loan_pre_loan_features') }} as features on features.loan_id = loans.loan_id
