-- One row per district (where clients live and where branches are).
select * from {{ ref('stg_districts') }}
