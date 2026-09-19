"""Generate the synthetic book: our own SLB and repo positions.

The market data is real; only our positions are invented, because a real desk's
book is not public. Everything here is drawn from actual quotes -- real symbols,
real series, real reverse-leg dates, real fees, real G-Secs -- so the positions
are ones that could genuinely have been traded on those days.

Seeded, so it reproduces. Idempotent: it truncates the book and regenerates.

Economics, so the book is not nonsense: the financing desk borrows stock from
lenders below the market fee and lends it on above, earning the fee spread. Delta
One is a pure consumer of borrow. Treasury holds no positions -- it is the other
side of every FTP charge.
"""

from __future__ import annotations

import datetime as dt
import random
import sys

from slbdesk import bonds, db, repo

SEED = 20260918
TRADES = 400
REPOS = 60

# The desk's own bid/offer around the market fee. A financing desk earns the
# spread between what it pays a lender and what it charges a borrower.
BORROW_FEE_FACTOR = 0.88
LEND_FEE_FACTOR = 1.14

# Everything derived from the book, not just the book itself. Regenerating the
# trades while leaving the P&L and FTP tables behind would leave rows keyed to
# trade_ids that no longer exist -- and because the analytics upsert rather than
# replace, the stale rows would survive the next refresh and quietly inflate the
# book. `make bootstrap` is what surfaced this: the list had not been updated
# when stage 6 added the P&L tables.
TRUNCATE = """
TRUNCATE
    desk_pnl_daily,
    ftp_charge_daily,
    slb_position_pnl_daily,
    margin_call,
    collateral_position,
    repo_trade,
    slb_trade_leg,
    slb_trade
RESTART IDENTITY
"""

# Real tradable combinations: a quote that actually printed, with an underlying
# price so the position has a notional, and enough trades to be a real level.
CANDIDATES = """
SELECT
    q.trade_date,
    q.symbol,
    q.series_code,
    q.reverse_leg_date,
    q.close_fee,
    c.close_price
FROM slb_quote_daily AS q
JOIN cash_quote_daily AS c
    ON c.trade_date = q.trade_date
   AND c.symbol = q.symbol
   AND c.series = 'EQ'
WHERE q.num_trades >= 3
  AND q.close_fee > 0
  AND q.contract_set IN ('REGULAR', 'NON_FORECLOSING')
ORDER BY q.trade_date, q.symbol, q.series_code
"""

# Real dated G-Secs to repo, with a traded clean price on some day. The coupon
# schedule comes along because day-one pricing needs accrued interest.
COLLATERAL = """
SELECT
    g.isin,
    t.trade_date,
    t.vwap_clean_price,
    t.weighted_ytm_pct,
    g.coupon_pct,
    g.next_ip_date,
    g.maturity_date,
    g.coupon_freq
FROM gsec AS g
JOIN gsec_trade_daily AS t
    ON t.security_code = g.security_code
   AND t.instrument_type = 'GS'
   -- Matched on coupon as well as code: CG2028 is shared by several bonds with
   -- different coupons, so joining on the code alone would collateralise a repo
   -- with a price that belongs to a different bond. Same trap the repo
   -- revaluation hit, and the reason both now key on the ISIN's own price.
   AND ABS(g.coupon_pct - REGEXP_REPLACE(t.issue_name, '[^0-9.]', '', 'g')::NUMERIC) < 1e-9
WHERE g.instrument_type = 'GS'
  AND g.maturity_date > t.trade_date + 365
ORDER BY t.trade_date, g.isin
"""


def next_business_day(date: dt.date) -> dt.date:
    """T+1 for the first leg, skipping the weekend.

    Exchange holidays are not modelled here: the seed only needs a settlement date
    that is plausibly one business day out, and the analytics read the date from
    the row rather than recomputing it.
    """
    date += dt.timedelta(days=1)
    while date.weekday() >= 5:
        date += dt.timedelta(days=1)
    return date


def status_for(reverse_leg_date: dt.date, latest: dt.date) -> str:
    return "CLOSED" if reverse_leg_date <= latest else "SETTLED_L"


def seed_slb(conn, candidates: list, latest: dt.date, rng: random.Random) -> int:
    """Matched pairs plus one-sided borrows, all priced off a real market fee."""
    rows = []
    for trade_date, symbol, series_code, reverse_leg_date, fee, price in rng.sample(
        candidates, min(TRADES, len(candidates))
    ):
        settle = next_business_day(trade_date)
        if settle >= reverse_leg_date:
            continue

        # SEBI caps total tenure at 12 months from the trade date, so the furthest
        # series is not always tradable: by 3-Sep-2026 the Sep-2027 series settles
        # 369 days out and is already out of reach. This is the NCL worked example
        # -- a position opened 01-Dec could roll no further than NOV of the next
        # year, because the DEC expiry falls beyond twelve months (docs/01 §6).
        # db/queries/validate_tenure.sql is what caught this being ignored.
        if (reverse_leg_date - trade_date).days > 365:
            continue

        # Size the position in whole shares off a target notional, so quantities
        # look like a desk's rather than like round numbers.
        notional = rng.uniform(2_000_000, 40_000_000)
        quantity = max(1, int(notional / float(price)))
        status = status_for(reverse_leg_date, latest)

        if rng.random() < 0.65:
            # Matched pair: borrow the stock in, lend it on at a wider fee.
            rows.append(
                (
                    "EQ_FIN",
                    symbol,
                    series_code,
                    "BORROW",
                    quantity,
                    trade_date,
                    settle,
                    reverse_leg_date,
                    round(float(fee) * BORROW_FEE_FACTOR, 4),
                    status,
                )
            )
            rows.append(
                (
                    "EQ_FIN",
                    symbol,
                    series_code,
                    "LEND",
                    quantity,
                    trade_date,
                    settle,
                    reverse_leg_date,
                    round(float(fee) * LEND_FEE_FACTOR, 4),
                    status,
                )
            )
        else:
            # Delta One shorting the index basket: a pure consumer of borrow.
            rows.append(
                (
                    "DELTA_ONE",
                    symbol,
                    series_code,
                    "BORROW",
                    quantity,
                    trade_date,
                    settle,
                    reverse_leg_date,
                    round(float(fee), 4),
                    status,
                )
            )

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO slb_trade (
                desk_id, symbol, series_code, side, quantity,
                trade_date, first_leg_settle_date, reverse_leg_date,
                fee_per_share, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        # A trade with no rollover is its own original, which keeps the
        # 12-month tenure check a single self-join (docs/06).
        cur.execute("UPDATE slb_trade SET original_trade_id = trade_id")
        # Every position opens with one leg; later re-rates append rather than
        # mutate, so the fee history stays reconstructible (docs/06).
        cur.execute(
            """
            INSERT INTO slb_trade_leg (
                trade_id, leg_type, effective_from, effective_to, quantity, fee_per_share, reason
            )
            SELECT
                trade_id,
                'OPEN',
                first_leg_settle_date,
                CASE WHEN status = 'CLOSED' THEN reverse_leg_date END,
                quantity,
                fee_per_share,
                'initial trade'
            FROM slb_trade
            """
        )
    return len(rows)


def seed_repo(conn, collateral: list, latest: dt.date, rng: random.Random) -> int:
    """G-Sec repo, priced off a real traded clean price.

    Day one is priced the same way the daily revaluation prices it: on the DIRTY
    value and with the haircut the tenor model gives. That consistency matters --
    an earlier version drew a random haircut and valued the collateral clean, and
    every position was then under-collateralised from inception against the
    model's own haircut, so 30% of days generated a margin call that was an
    artefact of the seed rather than a price move. Calls should come from the
    market, not from the book disagreeing with itself.
    """
    rows = []
    for (
        isin,
        trade_date,
        clean_price,
        ytm,
        coupon_pct,
        next_ip_date,
        maturity_date,
        coupon_freq,
    ) in rng.sample(collateral, min(REPOS, len(collateral))):
        tenor = rng.choice([1, 7, 14, 30, 91])
        start = next_business_day(trade_date)
        end = start + dt.timedelta(days=tenor)

        nominal = float(rng.choice([50, 100, 250, 500])) * 1_000_000
        haircut = repo.haircut_for_gsec((maturity_date - start).days / 365.0)
        # Repo trades a touch under the collateral's own yield: secured funding is
        # cheaper than the asset it is secured against.
        repo_rate = round(max(float(ytm) - rng.uniform(0.15, 0.80), 0.10), 4)

        period_start, _ = bonds.coupon_schedule(
            start, next_ip_date, maturity_date, coupon_freq or 2
        )
        accrued = bonds.accrued_interest(float(coupon_pct), period_start, start)
        dirty_value = nominal / 100 * (float(clean_price) + accrued)

        purchase = round(dirty_value * (1 - haircut), 4)
        repurchase = round(purchase * (1 + repo_rate / 100 * tenor / 365), 4)

        rows.append(
            (
                "REPO",
                rng.choice(["REPO", "REVERSE"]),
                isin,
                nominal,
                trade_date,
                start,
                end,
                repo_rate,
                haircut,
                purchase,
                repurchase,
                "MATURED" if end <= latest else "OPEN",
            )
        )

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO repo_trade (
                desk_id, direction, isin, nominal, trade_date, start_date, end_date,
                repo_rate_pct, haircut, purchase_price, repurchase_price, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    return len(rows)


def main() -> int:
    rng = random.Random(SEED)

    with db.connect() as conn:
        candidates = conn.execute(CANDIDATES).fetchall()
        collateral = conn.execute(COLLATERAL).fetchall()

        if not candidates:
            print("no market quotes loaded - run `make ingest` first")
            return 1

        latest = max(row[0] for row in candidates)
        conn.execute(TRUNCATE)

        slb = seed_slb(conn, candidates, latest, rng)
        repo = seed_repo(conn, collateral, latest, rng) if collateral else 0
        conn.commit()

        legs = conn.execute("SELECT COUNT(*) FROM slb_trade_leg").fetchone()[0]

    print(f"seeded {slb:,} SLB trades, {legs:,} legs, {repo:,} repo trades (seed={SEED})")
    if not repo:
        print("  no G-Sec trade prices loaded, so no repo book")
    return 0


if __name__ == "__main__":
    sys.exit(main())
