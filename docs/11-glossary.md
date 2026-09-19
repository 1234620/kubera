# 11 — Glossary

Terms a securities financing interview will use without defining them.

## Securities lending

**SLB / SBL** — Securities Lending and Borrowing / stock borrow-loan. In India, SLB
is NSE Clearing's CCP-cleared scheme.

**Lending fee** — what the borrower pays the lender. In NSE SLB it is quoted in
**rupees per share for the whole contract period**, not annualised and not a
percentage. Annualising it correctly is the first step of every analysis here.

**Rebate rate** — the other quoting convention, used in cash-collateralised markets
(the US in particular). The lender holds the borrower's cash collateral, earns
interest on it, and rebates part of that interest back to the borrower. A **negative
rebate** means the borrower pays on top of forgoing the interest — the security is
special. Fee-based and rebate-based quotes describe the same economics from
different sides.

**First leg** — the opening exchange: shares out, fee in. Settles T+1 in NSE SLB.

**Reverse leg** — the closing exchange, on the series' fixed settlement date.

**Series** — the contract identifying the reverse-leg settlement date. NSE SLB has
twelve monthly series in each of two parallel contract sets, plus 48 rollover series.

**Recall** — the lender asks for the shares back early.

**Repay** — the borrower returns them early.

**Rollover** — extending a position to a later series. Total tenure may not exceed
12 months from the original trade.

**Foreclosure** — a forced unwind triggered by a corporate action, with the fee
returned pro-rata for the unexpired period.

**Shut period** — the window around a corporate action during which no SLB
transactions, including rollovers, are allowed in that security.

**Buy-in auction** — what NCL runs when a borrower fails to return shares on the
reverse-leg day.

**Close-out** — cash settlement at a punitive formula price when delivery fails and
the auction cannot fix it.

**Utilisation** — on-loan value ÷ total lendable inventory. The industry's
supply-and-demand gauge.

**Lendable** — inventory made available to lend, whether or not it is on loan.

**On loan** — currently lent out.

**Days to cover** — on-loan quantity ÷ average daily cash volume. How long it would
take shorts to buy the stock back.

**Short interest** — shares sold short outstanding.

**VWAF** — value-weighted average fee: Σ(loan value × fee) ÷ Σ(loan value). The
standard way to express "what did this name cost to borrow today".

**DCBS** — Daily Cost of Borrow Score, a 1–10 vendor scoring of borrow expense
(S&P Global Market Intelligence). Our specialness score is the same idea on a 0–100
scale, computed from public data and with its components exposed.

**Re-rate** — repricing an open loan's fee without closing it.

## Repo

**Repo** — sale with an agreed repurchase. Economically a secured loan; legally two
outright trades. From the cash **borrower's** side.

**Reverse repo** — the same trade from the cash **lender's** side.

**GC — General Collateral** — the counterparty will accept anything from an agreed
basket. The trade is about cash, and it prices at the market's general secured
funding rate.

**Special** — a specific security in demand, so its repo rate trades *below* GC.

**Specialness** — `GC rate − special rate`, in bps. The implicit securities-borrowing
fee, economically a **convenience yield** on holding the scarce bond.

**Haircut** — the discount applied to collateral value: `cash = value × (1 − h)`.

**Initial margin** — the same protection expressed as a multiplier:
`cash = value / m`, with `m = 1/(1 − h)`.

**Variation margin** — the daily top-up as prices and haircuts move.

**Margin call** — the demand for that top-up. Under CCIL's TREPS rules a shortfall
must be met by 09:00 the next business day.

**Minimum transfer amount (MTA)** — the threshold below which a call is not made,
to avoid moving trivial sums.

**Triparty repo** — a third party (CCIL in India) holds and manages the collateral,
handling selection, valuation, substitution and margining. The two principals agree
only cash, rate and tenor.

**TREPS** — CCIL's triparty repo product, live since July 2018, mostly overnight,
and the dominant Indian money-market instrument by volume.

**Matched book** — borrowing and lending cash for the same tenor, earning the spread
with no deliberate maturity mismatch.

**Wrong-way risk** — collateral whose value falls precisely when the counterparty
defaults. Never take a bank's own paper against a loan to that bank.

**Rehypothecation** — re-pledging collateral received. Out of scope here, but the
term will come up.

## Bond math

**Clean price** — quoted price, excluding accrued interest.

**Dirty price** — clean price + accrued interest. The actual settlement amount, also
called the full or invoice price.

**Accrued interest** — coupon earned since the last coupon date. **30/360** for
Indian G-Secs.

**Day count convention** — the rule for converting a date range into a year
fraction. 30/360 for G-Sec accrual, ACT/365 for the rupee money market. Getting this
wrong is a silent error.

**YTM** — the single discount rate equating the present value of all remaining cash
flows to the dirty price. Assumes hold to maturity, reinvestment at the YTM itself,
and no default. The reinvestment assumption is the weak one.

**Current yield** — annual coupon ÷ clean price. Ignores capital gain or loss and
the time value of money; useful only as a YTM seed.

**Macaulay duration** — PV-weighted average time to receive the cash flows, in years.

**Modified duration** — `Macaulay / (1 + y/m)`. Percentage price change per unit
yield change. The number on a risk report, and the input to the Basel III
standardised market-risk charge in Indian banks.

**Convexity** — the second-order price/yield term. Positive for a plain bond, so
duration alone overstates the loss from a yield rise and understates the gain from a
fall.

**DV01 / PV01 / BPV** — rupee price change per basis point. Identical idea; PV01 is
often signed so that a positive value means a gain when rates fall.

**Pull to par** — a premium or discount bond's price drifting toward face value as
maturity approaches.

**G-Sec** — Government of India dated security. Semi-annual coupon, T+1 settlement.

**T-bill** — GoI discount instrument of 91, 182 or 364 days. No coupon.

**NDS-OM** — RBI's order-matching platform where most G-Sec secondary trading
happens.

**FIMMDA** — Fixed Income Money Market and Derivatives Association of India; sets
valuation methodology and conventions.

## Funding and FTP

**FTP — Funds Transfer Pricing** — the internal price at which a bank's Treasury
sells funding to, and buys it from, its business lines.

**Transfer rate** — that internal price for a specific instrument.

**Matched-maturity FTP** — pricing each instrument at the funding-curve point
matching its own maturity or repricing profile. The industry standard.

**Single-pool FTP** — one rate for everything. Simple, and it makes maturity
transformation look free.

**Term liquidity premium (TLP)** — the extra cost of borrowing long instead of
rolling short. The component most often omitted and most expensive to omit.

**Contingent liquidity charge** — the cost of holding a liquid buffer against
exposures that might not be unwindable when you need them to be.

**NIM — net interest margin** — interest earned minus interest paid, over earning
assets.

**Desk spread** — external rate earned − FTP rate charged. The desk's commercial
performance, free of funding-mix luck.

**Treasury spread** — FTP rate charged − actual cost of funds raised. The return on
deliberate maturity transformation.

**Balance sheet cost** — the capital and leverage-ratio charge for holding a
position, over and above its funding cost.

**Behavioural vs contractual maturity** — what a position is *expected* to do versus
what the contract says. FTP has to choose, and the choice is a modelling decision
worth being able to defend.
