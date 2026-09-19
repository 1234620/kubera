-- validate_matched_book_pays_little_ftp.sql — netting must actually net.
--
-- A matched pair (borrow a name in, lend the same name and series out, same
-- size) consumes almost no balance sheet, so its net notional must be zero and
-- it must attract no FTP row at all. Returns any (desk, symbol, series) that is
-- flat yet still carries a charge. An empty result is a pass.
--
-- Without this, FTP silently becomes a flat tax on turnover rather than a price
-- for the resource consumed, and a matched book looks loss-making.

SELECT
    f.as_of_date,
    f.desk_id,
    f.symbol,
    f.series_code,
    f.net_quantity,
    f.ftp_charge_inr
FROM ftp_charge_daily AS f
WHERE f.net_quantity = 0
  AND ABS(f.ftp_charge_inr) > 0.01;
