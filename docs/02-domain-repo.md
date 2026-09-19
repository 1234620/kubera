# 02 — Repo mechanics: haircuts, collateral valuation, margin

Sources: ICMA ERCC *Guide to Best Practice in the European Repo Market*, ICMA
*Demystifying Repo Haircuts* (Sept 2025), ICMA ERC *Haircuts and initial margins
in the repo market* (2012), CCIL Triparty Repo risk management documents, RBI
master directions on repo transactions.

## 1. What a repo is, mechanically

A repo is a sale with an agreed repurchase. Legally it is two outright trades; economically
it is a secured loan. The seller (**cash borrower**, "repo") delivers securities and
receives cash today, and reverses it at a fixed future date. The buyer (**cash
lender**, "reverse repo") receives the securities as collateral.

Two prices:

```
purchase price     = dirty_value_of_collateral × (1 − haircut)
repurchase price   = purchase price × (1 + repo_rate × days / 365)
```

`days/365` because the Indian rupee money market is **ACT/365**. The collateral is
valued **dirty** — clean price plus accrued interest — because that is what the
bond is actually worth on the day. Valuing collateral clean systematically
under-collateralises the lender by the accrued coupon, which on a 7.5% G-Sec five
months into a coupon period is over 3 points.

## 2. Haircut vs initial margin

They do the same job — over-collateralise the cash lender against the risk that
the collateral loses value before it can be liquidated after a default — but they
are expressed differently, and mixing them up is a classic error.

| | Haircut | Initial margin |
| --- | --- | --- |
| Definition | Collateral value is **discounted** | Collateral is **multiplied up** |
| Formula | `cash = value × (1 − h)` | `cash = value / m` |
| A 2% haircut | `h = 0.02` | `m = 1.0204` |

They are related by `m = 1/(1 − h)`. This repo uses **haircuts** throughout and
stores them as decimals (`0.0250` = 2.5%), converting to initial margin only where
a formula demands it.

Haircut size is driven by the collateral's price volatility, liquidity, the
liquidation horizon, and the correlation between collateral value and counterparty
credit (wrong-way risk — never take a bank's own paper against a loan to that bank).

### Where our haircuts come from

Not invented. CCIL sets security-specific haircuts from **Value at Risk over a
5-day holding period**, adjusted for pro-cyclicality, with multiplicands stepping
the number up for semi-liquid and illiquid securities. NSE Clearing publishes the
same shape of number daily for SLB-eligible equities in the VaR begin-day file
`C_VAR1_SLB_DDMMYYYY_1.DAT`, per ISIN: VaR margin, extreme loss margin, and the
total applicable rate.

So the haircut schedule in `haircut_schedule` is loaded from that file for
equities, and derived for G-Secs from a tenor-banded VaR estimate documented in
[`07-analytics-spec.md`](07-analytics-spec.md). Both paths record their source.

## 3. Daily revaluation and margin calls

Every business day:

1. **Revalue collateral** at today's mark-to-market price *and* today's haircut.
   Both move. A haircut widening on unchanged prices is a margin call, and desks
   that only re-run prices miss it.
2. Compute **collateral value after haircut** against the **outstanding
   obligation** — for a repo, the accreted repurchase value to date; for SLB, the
   margin requirement on the open position.
3. The difference is **net exposure**:
   ```
   net_exposure = obligation_value − post_haircut_collateral_value
   ```
4. If `net_exposure > threshold`, raise a **margin call** for the shortfall,
   rounded up to the minimum transfer amount.

CCIL's TREPS rule is the concrete version of step 4: if revaluation produces a
*borrowing limit shortfall*, the member must deposit additional collateral by
**09:00 the next business day**. That deadline is modelled — a call has a `due_at`
and a status, so the dashboard can show calls that are open, met, or breached.

**Variation margin** is this daily top-up. **Initial margin** is the day-one
over-collateralisation. They are separate columns; netting them hides which one
moved.

## 4. GC versus specials

**General Collateral (GC)** — the counterparty does not care which specific bond
it gets, only that it comes from an agreed basket. GC trades at the market's
general secured funding rate. The trade is about *cash*.

**Special** — the counterparty wants one specific security, usually because it is
short of it. When demand for a specific issue outruns supply, the borrower will
accept a *lower* return on its cash in order to get that bond. So:

```
specialness (bps) = GC repo rate − special repo rate
```

A positive spread is the implicit securities-borrowing fee, and economically it is
a **convenience yield** — an extra dividend accruing to whoever holds the scarce
bond and lends it out.

In the equity SLB market the same phenomenon shows up directly as a **high lending
fee** rather than as a depressed repo rate, because the fee is quoted explicitly.
That is why this project's specialness score works off the lending fee, and why
the cohort baseline is the market's own GC-equivalent fee level rather than an
external rate. Definition and formula: [`07-analytics-spec.md`](07-analytics-spec.md).

Drivers of a name going special: a hard catalyst (index rebalance, merger
arbitrage, convertible issuance, dividend arbitrage around a record date), shrinking
lendable supply, rising short interest, and rising utilisation. Utilisation and
days-to-cover are the early indicators; the fee is the confirmation.

## 5. Repo P&L

For a **matched book** — borrow cash cheap, lend it dearer against collateral —
the desk's gross return is the spread between the two repo rates on the same cash
for the same days:

```
gross_spread_pnl = notional × (reverse_repo_rate − repo_rate) / 100 × days / 365
```

Then subtract what the position actually costs to hold:

```
net_pnl = gross_spread_pnl − ftp_charge − balance_sheet_cost
```

`ftp_charge` comes from [`04-domain-ftp.md`](04-domain-ftp.md). This is where the
project joins up: a repo that looks profitable on the rate spread can be
loss-making once treasury charges it for term funding and the liquidity it
consumes, and that is precisely the calculation an FTP model exists to make
visible.

## 6. Risk on the collateral

A repo desk is not rate-neutral just because the trade is secured. Between today
and the repurchase date the collateral's price moves, and that movement is a
margin-call risk even when it is not a P&L risk. So the bond risk measures matter
here and not only on the trading book:

- **DV01** of the collateral pool sizes the margin call from a 1bp move
- **Convexity** tells you the call is asymmetric for large moves
- **Duration** of the pool against the repo tenor shows the maturity mismatch

Formulas: [`03-domain-bond-math.md`](03-domain-bond-math.md).

## 7. The Indian market specifics we model

| Market | What it is | Relevance |
| --- | --- | --- |
| **TREPS** (CCIL, since Jul 2018) | Triparty repo, mostly overnight, CCIL manages collateral | The GC benchmark; source of the short end of the funding curve |
| **Market repo** (CCIL) | Bilateral repo in G-Secs, CCIL-cleared | Term repo rates |
| **LAF / MSF** (RBI) | Repo and marginal standing facility with the central bank | The policy floor/ceiling the funding curve sits between |
| **SLB** (NSE Clearing) | Equity stock borrow-loan, CCP-cleared | The lending book |

Triparty is worth naming in an interview: a third party (CCIL) holds and manages
the collateral, does the selection, valuation, substitution and margining, so the
two principals only agree cash, rate and tenor. It is why TREPS volumes dominate
Indian money markets.
