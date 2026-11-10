-- Fact table: one row per loan (Q3: problem loans, Q4: indicators known before the loan).
select
    loans.loan_id,
    loans.account_id,
    accounts.owner_client_id,
    accounts.district_id,
    loans.granted_on,
    loans.amount,
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
