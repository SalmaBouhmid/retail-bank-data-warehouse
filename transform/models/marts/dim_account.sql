-- One row per account, with its owner, its branch district and its products.
with accounts as (
    select * from {{ ref('stg_accounts') }}
)

select
    accounts.account_id,
    owners.owner_client_id,
    accounts.district_id,
    districts.district_name,
    districts.region,
    accounts.opened_on,
    extract(year from age('{{ var("reference_date") }}'::date, accounts.opened_on))::int
        as seniority_years,
    accounts.statement_frequency,
    owners.n_clients,
    owners.has_secondary_user,
    coalesce(cards.n_cards, 0) as n_cards,
    coalesce(cards.best_card_type, 'none') as best_card_type,
    loans.loan_id is not null as has_loan
from accounts
inner join {{ ref('int_account_owners') }} as owners on owners.account_id = accounts.account_id
left join {{ ref('stg_districts') }} as districts on districts.district_id = accounts.district_id
left join {{ ref('int_account_cards') }} as cards on cards.account_id = accounts.account_id
left join {{ ref('stg_loans') }} as loans on loans.account_id = accounts.account_id
