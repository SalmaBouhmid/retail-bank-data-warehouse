-- One row per loan. Default (= problem loan) = status B or D (project definition,
-- CAHIER_DES_CHARGES.md section 3).
with loans as (
    select * from {{ source('raw', 'loan') }}
),

status_codes as (
    select code, label from {{ ref('czech_codes') }} where domain = 'loan_status'
)

select
    loans.loan_id,
    loans.account_id,
    loans.date as granted_on,
    loans.amount,
    loans.duration as duration_months,
    loans.payments as monthly_payment,
    loans.status as status_code,
    status_codes.label as loan_status,
    loans.status in ('B', 'D') as is_default,
    loans.status in ('A', 'B') as is_finished
from loans
left join status_codes on status_codes.code = loans.status
