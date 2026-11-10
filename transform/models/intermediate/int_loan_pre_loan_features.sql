-- Indicators about each loan's account computed ONLY with what the bank knew BEFORE the
-- loan was granted (Q4). Using later data would be a "data leak": for example, the loan
-- repayments (payment_type = loan_payment) only exist after the loan, so counting them
-- would "predict" the loan with information from the future.
--   * transactions and daily balances: strictly before the day of the loan
--   * monthly figures: only full months strictly before the month of the loan
-- The tests in tests/assert_pre_loan_*.sql check these limits.
with loans as (
    select
        loans.loan_id,
        loans.account_id,
        loans.granted_on,
        date_trunc('month', loans.granted_on)::date as loan_month,
        owners.owner_client_id
    from {{ ref('stg_loans') }} as loans
    inner join {{ ref('int_account_owners') }} as owners on owners.account_id = loans.account_id
),

transactions_before as (
    select
        loans.loan_id,
        count(*) as n_transactions,
        min(t.transaction_date) as first_transaction_date,
        max(t.transaction_date) as last_transaction_date,
        count(*) filter (where t.payment_type = 'penalty_interest') as n_penalty_interest,
        count(*) filter (where t.operation = 'cash_withdrawal') as n_cash_withdrawals,
        count(*) filter (where t.payment_type = 'old_age_pension') > 0 as receives_pension
    from loans
    inner join {{ ref('stg_transactions') }} as t
        on t.account_id = loans.account_id
        and t.transaction_date < loans.granted_on
    group by loans.loan_id
),

daily_before as (
    select
        loans.loan_id,
        min(d.end_of_day_balance) as min_balance,
        count(*) filter (where d.end_of_day_balance < 0) as n_days_negative_balance,
        (array_agg(d.end_of_day_balance order by d.balance_date desc))[1] as balance_before_loan,
        max(d.balance_date) as last_balance_date
    from loans
    inner join {{ ref('int_daily_balances') }} as d
        on d.account_id = loans.account_id
        and d.balance_date < loans.granted_on
    group by loans.loan_id
),

monthly_before as (
    select
        loans.loan_id,
        count(*) as n_full_months,
        max(m.month_start) as last_month_used,
        avg(m.month_end_balance) as avg_month_end_balance,
        avg(m.credits) as avg_monthly_credits,
        avg(m.debits) as avg_monthly_debits
    from loans
    inner join {{ ref('int_account_monthly_balances') }} as m
        on m.account_id = loans.account_id
        and m.month_start < loans.loan_month
    group by loans.loan_id
)

select
    loans.loan_id,
    extract(year from age(loans.granted_on, clients.birth_date))::int as client_age_at_loan,
    exists (
        select 1
        from {{ ref('stg_dispositions') }} as dispositions
        inner join {{ ref('stg_cards') }} as cards on cards.disp_id = dispositions.disp_id
        where dispositions.account_id = loans.account_id
          and cards.issued_on < loans.granted_on
    ) as had_card_before_loan,
    coalesce(t.n_transactions, 0) as n_transactions_before,
    (loans.granted_on - t.first_transaction_date) as days_of_history_before,
    t.last_transaction_date as last_transaction_date_before,
    coalesce(t.n_penalty_interest, 0) as n_penalty_interest_before,
    coalesce(t.n_cash_withdrawals, 0) as n_cash_withdrawals_before,
    coalesce(t.receives_pension, false) as received_pension_before,
    d.min_balance as min_balance_before,
    coalesce(d.n_days_negative_balance, 0) as n_days_negative_balance_before,
    d.balance_before_loan,
    d.last_balance_date as last_balance_date_before,
    coalesce(m.n_full_months, 0) as n_full_months_before,
    m.last_month_used as last_month_used_before,
    round(m.avg_month_end_balance, 2) as avg_month_end_balance_before,
    round(m.avg_monthly_credits, 2) as avg_monthly_credits_before,
    round(m.avg_monthly_debits, 2) as avg_monthly_debits_before
from loans
inner join {{ ref('stg_clients') }} as clients on clients.client_id = loans.owner_client_id
left join transactions_before as t on t.loan_id = loans.loan_id
left join daily_before as d on d.loan_id = loans.loan_id
left join monthly_before as m on m.loan_id = loans.loan_id
