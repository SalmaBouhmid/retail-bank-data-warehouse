-- The owner of each account (exactly one per account) and how many clients can use it.
select
    account_id,
    max(case when disposition_type = 'owner' then client_id end) as owner_client_id,
    count(*) as n_clients,
    count(*) filter (where disposition_type = 'user') > 0 as has_secondary_user
from {{ ref('stg_dispositions') }}
group by account_id
