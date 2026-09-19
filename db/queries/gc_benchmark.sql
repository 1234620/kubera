-- gc_benchmark.sql — the General Collateral equivalent for equity SLB.
--
-- Purpose : specialness is relative, so there must be something to be relative
--           to. Equity SLB publishes no GC rate, so we define one and document
--           it rather than borrowing a number from the repo market.
-- Inputs  : slb_fee_basis (view over slb_quote_daily + cash_quote_daily)
-- Grain   : one row per (as_of_date, contract_set)
-- Writes  : gc_rate_daily
-- Units   : gc_fee_per_share_median in rupees/share for the contract period;
--           gc_fee_annualised_pct in percent per annum (ACT/365)
--
-- Formula : the QUANTITY-WEIGHTED MEDIAN annualised fee across the day's liquid
--           cohort (num_trades >= 3 and traded_qty >= 1000), restricted to the
--           three nearest tenor buckets.
--
-- Why median and not mean: the fee distribution is severely right-skewed. A
-- handful of hard-to-borrow names at 40% p.a. drags a mean benchmark upward and
-- makes genuinely special names look ordinary.
-- Why quantity-weighted: a 100-share print should not move a market benchmark.
-- Below 20 cohort members the median of a dozen observations is not a market
-- rate, so the row is flagged is_estimated.

INSERT INTO gc_rate_daily (
    as_of_date,
    contract_set,
    gc_fee_per_share_median,
    gc_fee_annualised_pct,
    cohort_size,
    is_estimated
)
WITH liquid AS (
    SELECT
        b.trade_date,
        b.contract_set,
        b.fee_per_share,
        b.fee_annualised_pct,
        b.traded_qty
    FROM slb_fee_basis AS b
    WHERE b.num_trades >= 3
      AND b.traded_qty >= 1000
      AND b.fee_annualised_pct > 0
      AND b.tenor_days <= 270
),
-- Quantity weighting by expansion would be unbounded, so weight the percentile
-- with a running-share cut instead: order by fee, accumulate quantity, and take
-- the fee at which half the day's lent quantity sits below.
weighted AS (
    SELECT
        l.trade_date,
        l.contract_set,
        l.fee_per_share,
        l.fee_annualised_pct,
        SUM(l.traded_qty) OVER (
            PARTITION BY l.trade_date, l.contract_set
            ORDER BY l.fee_annualised_pct
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cum_qty,
        SUM(l.traded_qty) OVER (
            PARTITION BY l.trade_date, l.contract_set
        ) AS total_qty
    FROM liquid AS l
),
median AS (
    SELECT DISTINCT ON (w.trade_date, w.contract_set)
        w.trade_date,
        w.contract_set,
        w.fee_per_share,
        w.fee_annualised_pct
    FROM weighted AS w
    WHERE w.cum_qty >= w.total_qty / 2.0
    ORDER BY w.trade_date, w.contract_set, w.cum_qty
),
cohort AS (
    SELECT
        l.trade_date,
        l.contract_set,
        COUNT(*) AS cohort_size
    FROM liquid AS l
    GROUP BY l.trade_date, l.contract_set
)
SELECT
    m.trade_date,
    m.contract_set,
    m.fee_per_share,
    m.fee_annualised_pct,
    c.cohort_size,
    (c.cohort_size < 20) AS is_estimated
FROM median AS m
JOIN cohort AS c
    ON c.trade_date = m.trade_date
   AND c.contract_set = m.contract_set
ON CONFLICT (as_of_date, contract_set) DO UPDATE SET
    gc_fee_per_share_median = EXCLUDED.gc_fee_per_share_median,
    gc_fee_annualised_pct   = EXCLUDED.gc_fee_annualised_pct,
    cohort_size             = EXCLUDED.cohort_size,
    is_estimated            = EXCLUDED.is_estimated;
