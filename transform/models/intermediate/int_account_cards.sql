-- Cards of each account: count and best card type (gold > classic > junior).
select
    dispositions.account_id,
    count(*) as n_cards,
    {{ best_card_type('cards.card_type') }} as best_card_type
from {{ ref('stg_dispositions') }} as dispositions
inner join {{ ref('stg_cards') }} as cards on cards.disp_id = dispositions.disp_id
group by dispositions.account_id
