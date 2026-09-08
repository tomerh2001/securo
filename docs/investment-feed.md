# Investment collector integration

The optional investment collector connection consumes a cached, scoped feed. Clal
and Hachshara Best Invest are supported as separate source connections. It
creates one investment asset per actual product identity, with separate groups
for pension, keren hishtalmut, provident funds, and investments. It creates no bank accounts,
cash transactions, or share trades. Track balances and projected monthly
pensions are descriptive information and do not increase net worth twice.

Enable the provider with `INVESTMENT_FEED_ENABLED=true` and set
`INVESTMENT_FEED_URL` to the administrator-controlled `/investments/v1` endpoint.
Enable these settings in both the API and background worker. Connect using the
collector's investment access token through the normal token connection flow.
For Best Invest, configure `BEST_INVEST_FEED_URL` and use a
`best-invest.<token>` connection token. See [Best Invest setup](best-invest.md).
The selected URL is administrator-controlled; redirects are disabled.
The feed accepts source providers `clal` and `hachshara_best_invest`.

The feed contract is defined in `backend/app/schemas/investment_feed.py`.
Money is transported as exact decimal strings. Product, valuation, and activity
identities must be stable independently of names. Duplicate identities,
inconsistent currencies, malformed dates and amounts fail validation before
any records are changed. Source corrections update the same records, and older
observations cannot replace newer financial values.
Each product ID starts with its provider namespace (for example `clal:`), and
product/source providers must agree. Existing valuation, activity and track IDs
are opaque; they do not require the product prefix.
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
required provider verification retain the last known values and update the visible
source status. The bridge access token remains valid when a provider requires a new
verification code; replacing that token does not solve a provider login challenge. Scheduled
Securo pulls only read the cache and never request an SMS.

Unknown liquidity stays unknown. These assets contribute to investment and net
worth totals, while available-cash calculations continue to use cash accounts.
Source activity and valuation provenance are included in workspace exports.

## Account navigation and financial ownership

One collector connection owns multiple investment accounts. Each account is a
read model over its existing `Asset`, `AssetValue` and `AssetActivity` records;
it is not a second cash-ledger `Account`. Creating cash accounts with the same
balances would double-count net worth and produce incorrect transaction-based
balance history. Product groups remain optional collection memberships.

Accounts lists each product beneath its connection. `/connections/:id` is the
connection's focused destination, including its bank/card accounts if any.
The route uses a connection ID because a bank aggregator can span more than one
institution. `/accounts/investments/:id` opens a product's Overview, Activity and
Reports. Account labels use product kind and the verified final four account
identifier characters; original provider names are secondary details. Assets
keeps the portfolio total and compact account links, separate from share-trading
columns and controls. Modules and collection membership govern visible links.

`GET /api/investment-accounts` accepts an optional `connection_id` and excludes
archived/sold accounts by default. The detail endpoint accepts a direct asset ID.
`GET /api/investment-accounts/:id/activities` supports page/limit, kind and year;
its facets describe all nonzero account activity and remain stable while filtering.
All queries enforce workspace ownership, including connection filters. Converted
balances use real cached FX rates; missing conversion rates produce null values.

Current source status and balance valuation date remain separate. Reading a
cached feed does not make provider data fresh. Unknown balances are unavailable,
not zero. Month-only activity retains month precision. Reports are separate
provider period summaries; overlapping periods are not added together. Optional
raw provider descriptions are collapsed and obey privacy mode.

Older products can receive masked identifiers without recollection through
`enrich_account_identifiers(session, connection, validated_feed)`. It matches
existing workspace/connection/source/product identities and shares the sync lock.
It changes only masked identifier metadata; it cannot create accounts or modify
source status, values, activities, groups or bank transactions. Run it with an
already verified cached feed and check financial fingerprints before and after.

An existing investment connection cannot switch source providers: reconnect and sync reject a mismatched verified source before changing saved connection or financial data; use a separate connection for another provider.

The new UI copy is registered in every locale bundle with English fallback
wording, matching the existing investment-field fallback convention.

The fork publishes backend/frontend `latest` images only from a successful
`main` CI run. Deploy those published artifacts after migration backup and
normal service review; do not bind-mount application changes into production.
