-- One row per client. In this version of the data, gender and birth date are
-- already separate columns (the original 1999 files coded them inside "birth_number").
select
    client_id,
    gender,
    birth_date,
    district_id
from {{ source('raw', 'client') }}
