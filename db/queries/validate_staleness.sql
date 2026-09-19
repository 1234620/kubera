-- validate_staleness.sql — a score computed off an old quote must say so.
--
-- Returns rows where days_since_last_trade exceeds the threshold but is_stale is
-- FALSE, i.e. the flag and the data disagree. An empty result is a pass.
--
-- This guards the one thing that would make the specialness table quietly
-- dishonest: presenting a nine-day-old fee as today's market.

SELECT
    s.trade_date,
    s.symbol,
    s.series_code,
    s.quote_date,
    s.days_since_last_trade,
    s.is_stale
FROM slb_specialness_daily AS s
WHERE (s.days_since_last_trade > 3) <> s.is_stale;
