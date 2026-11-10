{{ config(materialized='table') }}
-- Balance of each account at the end of each day with at least one transaction.
--
-- Problem: when a day has several transactions, the order of transaction_id is NOT the
-- real order (e.g. account 1 on 1995-08-31: interest credited before the statement fee).
-- Rule ("chaining"): balance before a transaction = balance_after - signed_amount.
-- The last transaction of the day is the one that no other transaction of the same day
-- starts from. Tolerance 1 CZK because the amounts are rounded to whole crowns.
-- When the rule finds no single answer (rare, see balance_rule), we take the highest id.
with transactions as (
    select
        transaction_id,
        account_id,
        transaction_date,
        signed_amount,
        balance_after,
        balance_after - signed_amount as balance_before
    from {{ ref('stg_transactions') }}
),

-- For each transaction: does another transaction of the same day start from its balance?
followed as (
    select
        t.transaction_id,
        bool_or(abs(other.balance_before - t.balance_after) <= 1) as is_followed
    from transactions as t
    inner join transactions as other
        on other.account_id = t.account_id
        and other.transaction_date = t.transaction_date
        and other.transaction_id <> t.transaction_id
    group by t.transaction_id
),

flagged as (
    select
        t.*,
        not coalesce(followed.is_followed, false) as is_last_by_chaining
    from transactions as t
    left join followed on followed.transaction_id = t.transaction_id
),

ranked as (
    select
        *,
        row_number() over (
            partition by account_id, transaction_date
            order by is_last_by_chaining desc, transaction_id desc
        ) as rank_in_day,
        count(*) filter (where is_last_by_chaining)
            over (partition by account_id, transaction_date) as n_last_candidates,
        count(*) over (partition by account_id, transaction_date) as n_transactions,
        sum(signed_amount) filter (where signed_amount > 0)
            over (partition by account_id, transaction_date) as credits,
        -sum(signed_amount) filter (where signed_amount < 0)
            over (partition by account_id, transaction_date) as debits
    from flagged
)

select
    account_id,
    transaction_date as balance_date,
    balance_after as end_of_day_balance,
    n_transactions,
    coalesce(credits, 0) as credits,
    coalesce(debits, 0) as debits,
    case
        when n_transactions = 1 then 'single_transaction'
        when n_last_candidates = 1 then 'chaining'
        else 'fallback_highest_id'
    end as balance_rule
from ranked
where rank_in_day = 1
