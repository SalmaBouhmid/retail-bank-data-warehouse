-- One row per transaction (about 1 million). Amounts are positive in the raw data:
-- signed_amount puts a minus sign on debits, so that sums give the net movement.
with transactions as (
    select * from {{ source('raw', 'trans') }}
),

codes as (
    select domain, code, label from {{ ref('czech_codes') }}
),

renamed as (
    select
        trans_id as transaction_id,
        account_id,
        date as transaction_date,
        type as direction_code,
        operation as operation_code,
        nullif(trim(k_symbol), '') as payment_type_code,  -- blank code = unknown
        amount,
        balance as balance_after,
        bank as partner_bank,
        account as partner_account
    from transactions
)

select
    renamed.transaction_id,
    renamed.account_id,
    renamed.transaction_date,
    renamed.direction_code,
    directions.label as direction,
    renamed.operation_code,
    operations.label as operation,
    renamed.payment_type_code,
    payment_types.label as payment_type,
    renamed.amount,
    case when directions.label = 'debit' then -renamed.amount else renamed.amount end
        as signed_amount,
    renamed.balance_after,
    renamed.partner_bank,
    renamed.partner_account
from renamed
left join codes as directions
    on directions.domain = 'transaction_type' and directions.code = renamed.direction_code
left join codes as operations
    on operations.domain = 'transaction_operation' and operations.code = renamed.operation_code
left join codes as payment_types
    on payment_types.domain = 'payment_type' and payment_types.code = renamed.payment_type_code
