-- Demographic data of the 77 districts. The raw columns A2...A16 are named after
-- the PKDD'99 guide (see the description of each column in _staging.yml).
select
    district_id,
    a2 as district_name,
    a3 as region,
    a4 as inhabitants,
    a5 as municipalities_under_500,
    a6 as municipalities_500_1999,
    a7 as municipalities_2000_9999,
    a8 as municipalities_10000_plus,
    a9 as cities,
    a10 as urban_ratio_pct,
    a11 as average_salary,
    a12 as unemployment_rate_1995,
    a13 as unemployment_rate_1996,
    a14 as entrepreneurs_per_1000,
    a15 as crimes_1995,
    a16 as crimes_1996
from {{ source('raw', 'district') }}
