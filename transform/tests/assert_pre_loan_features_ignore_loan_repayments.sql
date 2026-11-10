-- Data leak check (Q4), seen from the other side: loan repayments (payment_type =
-- loan_payment) happen AFTER the loan. If one of them were dated before the loan of its
-- account, the "before" window would not be clean. Returns such repayments (expect 0 rows).
select
    transactions.transaction_id,
    transactions.transaction_date,
    loans.granted_on
from {{ ref('stg_transactions') }} as transactions
inner join {{ ref('stg_loans') }} as loans on loans.account_id = transactions.account_id
where transactions.payment_type = 'loan_payment'
  and transactions.transaction_date < loans.granted_on
