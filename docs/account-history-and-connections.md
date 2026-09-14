# Account history and connection recovery

Accounts has one primary navigation link. Bank, card, and investment details
share their page header and Overview, Activity, History, and Connection tabs.
Account selection stays inside the account pages.

## History semantics

The history-coverage endpoints describe records stored in the current workspace:

- `GET /api/accounts/{id}/history-coverage`
- `GET /api/investment-accounts/{id}/history-coverage`

Each stream includes its count, first and last date, monthly counts, source
availability, and archive indicators. A range does not assert completeness.
Month-only contribution dates remain month-only. A synchronization timestamp
does not establish the date of a bank balance.

Bank activity loads every transaction page. Card activity excludes synthetic
opening calibrations and describes charges rather than reconstructed debt.
`balance_semantics=next_statement_debit` identifies an issuer's next statement
amount. Credit availability cannot be derived from that amount.

SimpleFIN may explicitly provide `extra.transaction_date`, its
`transaction_date_kind`, and `extra.charge_date`. The adapter preserves the
occurrence date separately from the billing date and stores a normalized marker
for later edits. Manual billing overrides and linked issuer statements take
priority. Missing billing dates stay unknown. Existing occurrence dates are
not silently overwritten during sync; historical corrections require a
separately verified recovery pass.

## Durable updates

`POST /api/connections/{id}/operations` accepts `refresh`, `import`, or `recover`.
`GET` on the same path returns recent operations and their bounded event logs.
Operations belong to the workspace and survive navigation and worker restarts.
Only one operation per connection can be active. Celery owns transitions and
imports; browser polling never triggers a collection or import.

Refresh collects first and imports only after a newly completed successful or
partial collection. Import reads saved data without claiming a fresh provider
login. Failed collections do not silently import an old cache as success.
Results show counts added, including a distinct zero-new-records outcome.

Manual recovery uses the operation UUID as the collector's idempotent request
ID. The collector must advertise `manualVerificationAvailable`, use the same
provider identity, and preserve request tombstones across restart. Submit a
six-digit code to `POST /api/connections/{id}/operations/{operation}/verification`;
cancel with `DELETE` on that path. Both require workspace write access and the
matching active recovery challenge. Codes are forwarded once and are never
saved in the operation, event log, or response. Expired and interrupted attempts
remain visible, and existing source attempt limits still apply.

The API and Celery worker both require the separate provider control-token
secrets. The beat scheduler does not need them. Run database migrations before
starting the worker; it recovers unfinished operations every minute. Cached
source imports skip connections with active user operations.

Hapoalim Investments currently provides saved-data imports without independent
source refresh controls. Its history and current-value availability remain
separate. An unavailable source action must not be presented as a repair button.
