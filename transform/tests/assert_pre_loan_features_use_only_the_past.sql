-- Data leak check (Q4): every indicator of int_loan_pre_loan_features must use only data
-- from BEFORE the loan. Returns the loans that break the rule (the test passes with 0 rows).
select
    features.loan_id,
    loans.granted_on,
    features.last_transaction_date_before,
    features.last_balance_date_before,
    features.last_month_used_before
from {{ ref('int_loan_pre_loan_features') }} as features
inner join {{ ref('stg_loans') }} as loans on loans.loan_id = features.loan_id
where features.last_transaction_date_before >= loans.granted_on
   or features.last_balance_date_before >= loans.granted_on
   or features.last_month_used_before >= date_trunc('month', loans.granted_on)
