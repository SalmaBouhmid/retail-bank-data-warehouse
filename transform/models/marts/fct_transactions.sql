-- Fact table: one row per transaction (about 1 million). Keys to the dimensions:
-- account_id -> dim_account, owner_client_id -> dim_client, district_id (branch of the
-- account) -> dim_district, transaction_date -> dim_date.
select
    transactions.transaction_id,
    transactions.account_id,
    accounts.owner_client_id,
    accounts.district_id,
    transactions.transaction_date,
    transactions.direction,
    transactions.operation,
    transactions.payment_type,
    transactions.amount,
    transactions.signed_amount,
    transactions.balance_after,
    transactions.partner_bank
from {{ ref('stg_transactions') }} as transactions
inner join {{ ref('dim_account') }} as accounts on accounts.account_id = transactions.account_id
