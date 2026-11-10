-- The fact table must keep every staging transaction (the joins must not drop rows).
select
    (select count(*) from {{ ref('stg_transactions') }}) as staging_rows,
    (select count(*) from {{ ref('fct_transactions') }}) as fact_rows
where (select count(*) from {{ ref('stg_transactions') }})
   <> (select count(*) from {{ ref('fct_transactions') }})
