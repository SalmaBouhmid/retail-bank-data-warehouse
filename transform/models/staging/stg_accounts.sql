-- One row per bank account. Codes translated with the seed czech_codes.
with accounts as (
    select * from {{ source('raw', 'account') }}
),

frequency_codes as (
    select code, label from {{ ref('czech_codes') }} where domain = 'account_frequency'
)

select
    accounts.account_id,
    accounts.district_id,
    accounts.date as opened_on,
    accounts.frequency as statement_frequency_code,
    frequency_codes.label as statement_frequency
from accounts
left join frequency_codes on frequency_codes.code = accounts.frequency
