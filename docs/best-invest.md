# Hachshara Best Invest

Best Invest uses the Investment collector connection and the existing investment
account screens. Create a separate connection for Best Invest even if Clal is
already connected.

## Configure Securo

Set these variables on both the backend and background worker:

```dotenv
INVESTMENT_FEED_ENABLED=true
INVESTMENT_FEED_URL=http://israeli-banks-bridge:8080/investments/v1
BEST_INVEST_FEED_URL=http://israeli-banks-bridge:8080/investments/best-invest/v1
```

Keep `INVESTMENT_FEED_URL` pointed at the existing Clal feed. Best Invest uses its
own URL and scoped access token. The backend selects the configured endpoint;
pasted tokens never supply a URL.

Best Invest values are in Israeli shekels (`ILS`). If deployment configuration
overrides `SUPPORTED_CURRENCIES`, include `ILS` alongside the other required
currencies. Choose ILS as the workspace display currency if desired. Cross-currency
totals require an available exchange rate; a missing rate remains unavailable.

## Connect and reconnect

1. Complete Best Invest collection in the collector.
2. Mint or retrieve its scoped Best Invest access token.
3. Open **Accounts > Connect bank > Investment collector**.
4. Paste `best-invest.<token>`, replacing `<token>` with that access token and
   keeping the `best-invest.` prefix. The dialog shows **Hachshara Best Invest**.
5. Select **Connect**.

Use a fresh token from the matching provider when reconnecting. A Best Invest
token cannot replace an existing Clal connection. Provider login verification
belongs in the collector; replacing a Securo connection token does not resolve
a provider login challenge.

## What appears in Securo

Each policy appears once as an investment account and contributes its policy
balance once to net worth. Tracks describe allocations within that policy;
they do not create extra assets. Provider activity and reports appear when supplied by the collector. Best Invest
deposits are currently period summaries, rather than invented individual
transactions. These reports never create additional bank transactions.

The valuation date and the collector's freshness/status are shown separately.
Reading the cached feed does not make a provider valuation newer. Failed or
incomplete collection preserves the last known value and reports the source
state; missing policies are not silently removed.

See [the investment feed contract](investment-feed.md) for validation, identity
and accounting rules.
