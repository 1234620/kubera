# 12 — Interview notes

How to talk about this project, and the domain questions it prepares you for.
Written for the Barclays FIF and EFG Quant JDs, but it generalises to any
securities financing, prime brokerage, or delta-one financing role.

## The 60-second version

> "I built a securities financing desk's analytics stack on real Indian market
> data. NSE Clearing publishes the securities lending and borrowing market daily —
> lending fees, open interest, eligibility and margin rates — and NSE publishes the
> G-Sec master and daily debt trades. I ingest those, model a stock-borrow-loan book
> in Postgres, and compute what a desk actually needs: utilisation, a specialness
> score, financing spread P&L attributed into fee spread, term spread and funding
> drag, plus the bond leg — accrued interest on the Indian 30/360 convention,
> clean/dirty price, YTM, duration, convexity, DV01 — and the repo side with
> haircuts, collateral revaluation and margin calls. On top of that there's a
> matched-maturity Funds Transfer Pricing model that allocates internal funding cost
> across desks, and the decomposition reconciles to book P&L to the rupee. FastAPI
> serves it, there's a dashboard and a Power BI model, and it all runs in Docker with
> CI."

Then stop. Let them pick the thread.

## The three things that make it credible

1. **The data is real and obscure.** Most candidate projects use Yahoo Finance
   closes. This uses the NSE SLB bhavcopy, the open-position file, the eligibility
   file with its per-series recall and repay flags, the corporate-action foreclosure
   report, and the begin-day VaR file. Column mappings that NSE does not document
   were verified against adjacent trading days — the previous-close field was
   confirmed by matching it to the prior day's close across every overlapping row.
   That is the difference between reading a spec and doing the work.

2. **Conventions are stated, not assumed.** 30/360 for G-Sec accrual. ACT/365 for
   repo and fee accrual. Fees are contract-period rupees per share, annualised
   explicitly and never mixed in one column. Accrual starts at first-leg settlement,
   not trade date. Positive P&L is always a gain to our book. Every one of those is a
   place people are silently wrong.

3. **The numbers check themselves.** Bond YTM is validated against NSE's own
   published weighted YTM, not just against a textbook. FTP reconciles to book P&L
   to ₹1. P&L attribution sums to net spread to a paisa. FTP charges across desks
   net to zero. Validation queries run on every analytics refresh and fail loudly.

## Domain questions this prepares you for

**"Walk me through the lifecycle of a stock loan."**
Order → match at T → custodial confirmation → obligation download → first-leg
pay-in T+1 08:00 → securities pay-out 10:00 → fee pay-out 11:30 → position live and
fee accruing → optionally re-rated, recalled, repaid or rolled → reverse-leg pay-in
on the series date → buy-in auction if the borrower fails → close-out if the auction
fails. Name where the CCP sits: in India, NSE Clearing is the counterparty on both
legs, so there is no bilateral credit exposure and the risk is funding and fee risk.

**"What makes a security special?"**
Demand for that specific name outruns lendable supply. In repo the fee shows up as a
rate *below* GC, and `GC − special` is the implicit borrow fee — economically a
convenience yield on holding the scarce bond. In equity SLB it shows up directly as
a high lending fee because the fee is quoted explicitly. Drivers: index rebalances,
merger arbitrage, convertible issuance, dividend arbitrage around a record date,
shrinking free float. Utilisation and days-to-cover lead; the fee confirms.

**"How would you measure specialness?"**
Never on the absolute fee — it is meaningless without a cohort and a history. Two
components: the cross-sectional percentile of the annualised fee against every other
name at the same tenor today, and a z-score against the security's own trailing
20-day mean, with the window *excluding* today so the baseline is not contaminated by
the spike you are detecting. Blend them, store both, and be able to say "94th
percentile and 2.1 sigma above its own mean" rather than just "87".

**"Clean price versus dirty price, and which one prices a bond?"**
Clean excludes accrued interest and is what gets quoted, so the quote does not
saw-tooth on coupon dates. Dirty is clean plus accrued and is what settles. YTM is
solved from the *dirty* price, because that is the actual cash paid. Using clean
gives a wrong yield. For Indian G-Secs accrued is 30/360.

**"Explain duration, and then explain what it misses."**
Macaulay duration is the PV-weighted average time to the cash flows, in years.
Modified duration divides it by `(1 + y/m)` and gives percentage price change per
unit yield. What it misses: it is a first-order approximation, linear in yield, so
for large moves you need convexity — and because convexity is positive, duration
overstates the loss from a rise and understates the gain from a fall. It also assumes
a parallel curve shift, which is why key-rate durations exist.

**"What's a haircut and how would you set one?"**
The discount on collateral value protecting the cash lender against price decline
before liquidation after a default: `cash = value × (1 − h)`. Set it from the
collateral's price volatility over the expected liquidation horizon — CCIL uses
security-specific VaR over a 5-day holding period, adjusted for pro-cyclicality, with
multiplicands stepping it up for semi-liquid and illiquid paper. Then consider
wrong-way risk: never take a counterparty's own paper against a loan to that
counterparty.

**"What is Funds Transfer Pricing and why does it matter to a financing desk?"**
It is the internal price at which Treasury sells funding to the business. It matters
here because funding *is* the product — the desk's economics are the spread between
the external rate it earns and the internal rate it is charged, so FTP is cost of
goods sold, not an overhead allocation. Matched-maturity is the standard: price each
position at its own point on the funding curve, plus a term liquidity premium.
Single-pool FTP charges an overnight asset and a ten-year asset the same rate, which
makes maturity transformation look free and over-incentivises it. The decomposition
is the deliverable: desk spread is external minus FTP, treasury spread is FTP minus
actual cost of funds, and they must add back to NIM — so you can tell whether a book
made money because it was good or because funding was cheap.

**"How do corporate actions affect a stock loan?"**
Dividend: NCL debits the borrower and credits the lender; the contract continues.
Stock split: quantity adjusts proportionately; the contract continues. Everything
else — bonus, merger, open offer, buy-back, demerger — foreclosed on ex-date with the
fee returned pro-rata. AGM/EGM is the interesting one: the regular series `01`–`12`
are force-foreclosed, but the parallel `X1`–`XD` set is not, which is exactly why two
contract sets exist. A shut period blocks all activity including rollovers, starting
ex-date − 1 for dividends and splits and ex-date − 7 for the rest.

**"You have a borrow at 2% and you lend it at 2.4%. Are you making money?"**
Not necessarily, and this is the FTP question in disguise. Forty basis points gross,
but if the position is 9-month term and the funding curve plus term liquidity premium
charges 55bp at that tenor, the desk is losing 15bp. Then check the balance sheet
cost on top. Also check whether the borrow can be recalled — a position you cannot
unwind early carries a contingent liquidity charge, and the NSE eligibility file
tells you per series whether recall is even permitted.

## Questions to ask them

- Is FTP matched-maturity, and how is the term liquidity premium calibrated?
- Where does the specialness call sit — is the trader pricing it, or is there a
  model, and who owns the model?
- How much of the book is agency versus principal?
- What does the desk do about the balance sheet charge at quarter end?
- How is the internal funding curve built, and how often does it move?

## Things not to claim

The book is synthetic — the *market* data is real, but our positions are generated,
and say so unprompted. `lendable_qty` is an estimate because NSE does not publish it,
and the estimate is flagged in the data. There is no real-time data; everything is
end-of-day. Owning those three limits up front is more convincing than being caught
on any of them.
