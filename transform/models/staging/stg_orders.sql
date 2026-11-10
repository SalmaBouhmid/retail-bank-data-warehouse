-- Permanent orders (always money going out of the account).
with orders as (
    select * from {{ source('raw', 'order') }}
),

payment_codes as (
    select code, label from {{ ref('czech_codes') }} where domain = 'payment_type'
)

select
    orders.order_id,
    orders.account_id,
    orders.bank_to as recipient_bank,
    orders.account_to as recipient_account,
    orders.amount,
    nullif(trim(orders.k_symbol), '') as payment_type_code,  -- blank code = unknown
    payment_codes.label as payment_type
from orders
left join payment_codes on payment_codes.code = nullif(trim(orders.k_symbol), '')
