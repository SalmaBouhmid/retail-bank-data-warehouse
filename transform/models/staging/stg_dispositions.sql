-- Link between clients and accounts: who is the owner, who is only a user of an account.
with dispositions as (
    select * from {{ source('raw', 'disp') }}
),

type_codes as (
    select code, label from {{ ref('czech_codes') }} where domain = 'disposition_type'
)

select
    dispositions.disp_id,
    dispositions.client_id,
    dispositions.account_id,
    dispositions.type as disposition_type_code,
    type_codes.label as disposition_type
from dispositions
left join type_codes on type_codes.code = dispositions.type
