-- One row per client, with the attributes used to segment the clientele (Q1, Q5).
-- Ages are computed at the reference date (end of the data), not at today's date.
with clients as (
    select
        *,
        extract(year from age('{{ var("reference_date") }}'::date, birth_date))::int
            as age_at_reference_date
    from {{ ref('stg_clients') }}
),

owned as (
    select owner_client_id as client_id, count(*) as n_accounts_owned
    from {{ ref('int_account_owners') }}
    group by owner_client_id
),

client_cards as (
    select
        dispositions.client_id,
        count(*) as n_cards,
        {{ best_card_type('cards.card_type') }} as best_card_type
    from {{ ref('stg_dispositions') }} as dispositions
    inner join {{ ref('stg_cards') }} as cards on cards.disp_id = dispositions.disp_id
    group by dispositions.client_id
),

client_loans as (
    select distinct dispositions.client_id
    from {{ ref('stg_dispositions') }} as dispositions
    inner join {{ ref('stg_loans') }} as loans on loans.account_id = dispositions.account_id
    where dispositions.disposition_type = 'owner'
)

select
    clients.client_id,
    clients.gender,
    clients.birth_date,
    clients.age_at_reference_date,
    case
        when clients.age_at_reference_date < 18 then '0-17'
        when clients.age_at_reference_date < 25 then '18-24'
        when clients.age_at_reference_date < 35 then '25-34'
        when clients.age_at_reference_date < 45 then '35-44'
        when clients.age_at_reference_date < 55 then '45-54'
        when clients.age_at_reference_date < 65 then '55-64'
        else '65+'
    end as age_band,
    clients.district_id,
    districts.district_name,
    districts.region,
    coalesce(owned.n_accounts_owned, 0) as n_accounts_owned,
    coalesce(client_cards.n_cards, 0) as n_cards,
    coalesce(client_cards.best_card_type, 'none') as best_card_type,
    client_loans.client_id is not null as has_loan
from clients
left join {{ ref('stg_districts') }} as districts on districts.district_id = clients.district_id
left join owned on owned.client_id = clients.client_id
left join client_cards on client_cards.client_id = clients.client_id
left join client_loans on client_loans.client_id = clients.client_id
