-- One row per card issued (Q5: who holds which card).
select
    cards.card_id,
    cards.card_type,
    cards.issued_on,
    dispositions.client_id,
    dispositions.account_id,
    dispositions.disposition_type as holder_role
from {{ ref('stg_cards') }} as cards
inner join {{ ref('stg_dispositions') }} as dispositions on dispositions.disp_id = cards.disp_id
