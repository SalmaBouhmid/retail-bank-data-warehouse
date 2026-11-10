-- One row per card. A card belongs to a disposition (a client on an account).
select
    card_id,
    disp_id,
    type as card_type,
    issued as issued_on
from {{ source('raw', 'card') }}
