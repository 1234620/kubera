# 01 — Securities Lending & Borrowing: the trade lifecycle

Sources: NSE Clearing *FAQ for SLB Scheme* (July 2025), NCL circular 61810
(29 Apr 2024), NCL circular 36465 (11 Dec 2017), SEBI SLB framework.

## 1. What the trade is

A lender gives shares to a borrower and gets them back later. The borrower pays a
**lending fee**, quoted in **rupees per share for the whole contract period** —
not annualised, not a percentage. In India this is not bilateral: NSE Clearing
(NCL) is the central counterparty, so there is no counterparty credit exposure
between lender and borrower. That single fact shapes the whole model — the
counterparty on every leg is NCL, and the risk the desk manages is *funding and
fee* risk, not default risk.

Who lends: institutions and retail holders with idle inventory wanting incremental
yield. Who borrows: short sellers, arbitrageurs closing a cash-futures basis,
market makers covering a delivery obligation, and anyone facing a settlement
shortfall.

## 2. Eligibility

NCL publishes the eligible list monthly (circular on the 20th) and daily as
`SLB_ELG_SEC_DDMMYYYY.csv`. A security qualifies if it is:

- available in NSE's F&O segment, **or**
- an index ETF traded on ≥80% of days over 6 months with impact cost ≤1%, **or**
- a SEBI "Group I" scrip with Market Wide Position Limit ≥ ₹100 crore **and**
  average monthly cash-market turnover ≥ ₹100 crore over the previous six months.

The daily file also carries per-series **Normal / Recall / Repay eligibility**
flags (`E` eligible, `D` disabled). Those flags matter: a series where recall is
disabled cannot be unwound early, which changes the economics of the position and
is exactly the kind of thing that gets missed.

## 3. Series — the part everyone gets wrong

Tenure runs **up to 12 months**, as **12 fixed monthly series** with a fixed
reverse-leg settlement date per series, published on the trading screen.

There are **two parallel contract sets** for every security:

| Set | Series codes | Behaviour on AGM/EGM |
| --- | --- | --- |
| Regular | `01` … `12` (Jan … Dec) | **Force-foreclosed** |
| Non-foreclosing | `X1` … `X9`, `XO`, `XN`, `XD` | **Not foreclosed**; runs to expiry |

In the `X` set the month is encoded as a digit for Jan–Sep (`X1`…`X9`) and a
letter for Oct/Nov/Dec (`XO`, `XN`, `XD`). Both sets are still foreclosed for
corporate actions other than dividend and stock split.

On top of that there are **48 rollover series** — 24 for the regular set
(two-letter codes) and 24 for the `X` set (alphanumeric codes). Each rollover
series encodes *both* a source series and a target series, so "roll my September
position to November" is its own tradable contract. Only near-month rollovers are
live at any time, and their last trading day is the fourth working day before the
source series expires.

**Consequence for the data model:** the series code alone does not give you a
tenor. You must join to the observed reverse-leg settlement date, which the
bhavcopy publishes per row. `slb_series` is therefore populated *from the data*,
not from a hardcoded table — see [`06-data-model.md`](06-data-model.md).

## 4. Settlement timeline

| When | What |
| --- | --- |
| **T** 09:15–17:00 | SLB trading session |
| T 17:15 | CP code modification cut-off |
| T 18:00 | Custodial confirmation |
| T 19:00 | Final obligation download to participants |
| **T+1** 07:30 | Client direct payout requests uploaded |
| T+1 08:00 | **First leg pay-in** of securities and funds (settlement type `L`) |
| T+1 10:00 | First leg securities pay-out — borrower now has the shares |
| T+1 11:30 | First leg funds pay-out — lender now has the fee |
| **Reverse leg day** 09:00 | **Reverse leg pay-in** of securities (settlement type `P`) |
| Reverse leg 11:30 | Reverse leg pay-out |
| Reverse leg 14:00 | Buy-in auction if the borrower failed to return |
| Reverse leg 16:30 | Auction obligation download |
| **RL+1** 09:00 | Auction settlement pay-in (settlement type `Q`) |

So a position exists from **T+1 pay-out** to **reverse-leg pay-in**, and the fee
accrues over that window. Using trade date instead of first-leg settlement date
overstates the accrual by a day on every trade.

## 5. Early unwind: recall and repay

- **Recall** — the *lender* wants the shares back early.
- **Repay** — the *borrower* wants to return them early.

Rules that bite:
- Only possible once the **first leg has settled** (so from T+1 onwards), and only
  against an existing reverse-leg position. Partial quantity allowed.
- Cut-off is **three working days before** the reverse-leg settlement date.
- The fee for recall/repay is **market determined**: the participant quotes the
  fee per share it is willing to forgo (lender) or demand (borrower) for the
  balance tenure. These are matched on a best-efforts basis — there is no
  guarantee an early unwind fills.
- For a repay the borrower must first move the shares into NCL's repayment account
  and allocate the early pay-in to clients before the repay request can be entered.
  If the repay does not match by the reverse-leg date, **the borrower forfeits the
  fee for the balance period**.

The economics: an unwind is a *new price* for the remaining tenure. Marking a
position at the original fee once a recall market exists is a stale mark.

## 6. Rollover

Permitted multiple times, subject to total tenure ≤ 12 months from the **original**
trade date. Worked example from the NCL FAQ: a position opened 08-Dec-2017 may roll
no further than the Dec-2018 series; opened 01-Dec-2017, the last permissible is
Nov-2018, because the Dec-2018 expiry falls after 01-Dec-2018.

## 7. Corporate actions

| Action | Treatment |
| --- | --- |
| **Dividend** | On record date +1 working day NCL debits the dividend on borrowed shares from the borrower and credits the lender. Contract continues. |
| **Stock split** | Borrower's position adjusted proportionately; lender receives the revised quantity at reverse leg. Contract continues. |
| **Everything else** (bonus, merger, amalgamation, open offer, buy-back, demerger) | **Foreclosed on ex-date.** Lender returns the fee pro-rata for the unexpired period, and NCL passes it to the borrower. |
| **AGM/EGM** | Foreclosed for series `01`–`12`; **not** foreclosed for `X1`–`XD`. |

**Shut period** — no trading, including rollover, in that security:
- dividend or stock split: shut period starts ex-date − 1 business day
- other foreclosing actions: ex-date − 7 days

NCL publishes `Forclosure_SLB_YYYYMMDD.CSV` with announcement date, record date,
ex date, foreclosure date, shut period start/end, next trade date, foreclosure
settlement number and the action description. That file is the desk's early
warning: a foreclosure is an unplanned unwind of a position at a pro-rata fee,
which is a P&L event.

## 8. Failures

**Lender fails first-leg delivery** → close-out at the higher of:
- 25% of the T+1 closing price in the cash market, or
- (max cash-market trade price T to T+1) − (T+1 closing price)

**Borrower fails reverse-leg delivery** → buy-in auction on the reverse-leg day,
settled on auction+1. No offer, or failure in the auction → close-out at the higher of:
- max cash-market trade price from (RL − 1) to RL, or
- 25% above the RL-day cash-market closing price

**Borrower funds shortage** → transaction cancelled; securities returned to the
lender with the fee. Same treatment applies to recall/repay transactions.

## 9. Collateral and margin

Participants post liquid assets to NCL: cash, bank guarantees, FDRs, GoI
securities and T-bills, equity shares and units of ETFs/open-ended mutual funds,
pledged in demat form. FDRs and bank guarantees issued by the member itself or an
associate bank are not accepted.

Security-level risk parameters come from `C_VAR1_SLB_DDMMYYYY_1.DAT` — the VaR
begin-day file — which supplies per-ISIN VaR margin, extreme loss margin and the
applicable total margin rate. That file is the input to this project's haircut
schedule; see [`02-domain-repo.md`](02-domain-repo.md).

## 10. The state machine we model

```
                  ┌──────────────┐
      order ─────▶│   MATCHED    │  (T, trade captured)
                  └──────┬───────┘
                         │ T+1 08:00 first-leg pay-in
                  ┌──────▼───────┐
                  │  SETTLED_L   │  position live, fee accruing
                  └──┬───┬───┬───┘
        recall/repay │   │   │ corporate action on ex-date
              ┌──────▼┐  │  ┌▼──────────────┐
              │UNWOUND│  │  │  FORECLOSED   │ (fee returned pro-rata)
              └───────┘  │  └───────────────┘
                         │ rollover (tenure ≤ 12m from original)
                  ┌──────▼───────┐
                  │  ROLLED      │──▶ new series, same original_trade_id
                  └──────┬───────┘
                         │ reverse-leg pay-in
                  ┌──────▼───────┐        fail
                  │   CLOSED     │◀──────── AUCTION ──▶ CLOSED_OUT
                  └──────────────┘
```

`slb_trade.status` takes exactly these values. Anything that changes the fee or
the tenure writes a new row in `slb_trade_leg` rather than mutating the trade, so
the fee history of a position is reconstructible — which is what you need to
attribute P&L to a re-rate rather than to the original trade.
