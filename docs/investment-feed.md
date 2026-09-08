# Investment collector integration

The optional Clal connection consumes a cached, scoped investment feed. It
creates one investment asset per actual product identity, with separate groups
for pension, keren hishtalmut, and provident funds. It creates no bank accounts,
cash transactions, or share trades. Track balances and projected monthly
pensions are descriptive information and do not increase net worth twice.

Enable the provider with `INVESTMENT_FEED_ENABLED=true` and set
`INVESTMENT_FEED_URL` to the administrator-controlled `/investments/v1` endpoint.
Enable these settings in both the API and background worker. Connect using the
collector's investment access token through the normal token connection flow.
The URL is never taken from the pasted token; redirects are disabled.

The feed contract is defined in `backend/app/schemas/investment_feed.py`.
Money is transported as exact decimal strings. Product, valuation, and activity
identities must be stable independently of names. Duplicate identities,
inconsistent currencies, malformed dates and amounts fail validation before
any records are changed. Source corrections update the same records, and older
observations cannot replace newer financial values.
Each product explicitly identifies its current valuation. Retrieving older
history cannot silently replace a current balance with an older figure.

Source valuation dates are preserved. If a source explicitly reports no
valuation date, Securo records an observation-day snapshot with
`source_as_of_verified=false` and the actual observation timestamp. That is not
an invented historical valuation. A later dated source value supersedes an
older undated observation. Month-only contribution dates stay `YYYY-MM`.

Savings activity preserves employee, employer and severance contributions,
withdrawals, transfers, management fees, insurance costs and source-reported
returns separately. It never duplicates salary or creates a second bank debit.
No return percentage is inferred from balance growth when contribution history
is incomplete. Verified counterpart bank payments can retain their existing
investment-transfer classification independently of this ledger.

Incomplete inventory never closes or deletes assets. Failed collection and
required Clal verification retain the last known values and update the visible
source status. The bridge access token remains valid when Clal requires a new
SMS code; replacing that token does not solve a Clal login challenge. Scheduled
Securo pulls only read the cache and never request an SMS.

Unknown liquidity stays unknown. These assets contribute to investment and net
worth totals, while available-cash calculations continue to use cash accounts.
Source activity and valuation provenance are included in workspace exports.

The Assets page shows savings activity across the portfolio and inside expanded
products, with contribution-month precision and signed costs preserved. Value
history labels undated observations explicitly. Source balance changes never
render as investment return percentages. English and Portuguese translations
are supplied; other locale bundles use English fallback copy for the new fields.

The fork publishes backend/frontend `latest` images only from a successful
`main` CI run. Deploy those published artifacts after migration backup and
normal service review; do not bind-mount application changes into production.
