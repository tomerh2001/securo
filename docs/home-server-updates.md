# Automatic home server updates

The customized fork follows stable upstream releases through a reviewed integration
path. A host task checks every five minutes, prepares an integration pull request,
waits for all required checks, and merges the tested commit while retaining upstream
ancestry. Conflicts or changes to the update machinery require review. Nothing
executes code from an unmerged upstream candidate on the host.

Successful full CI on the current fork `main` triggers **Publish Home Server
Images**. Backend and frontend build under immutable `sha-<revision>` tags. The
promotion job rechecks CI and the current branch before moving both `latest` tags.
The publisher serializes queued runs with `queue: max`: disabling cancellation
alone still allows a newer event to replace the pending run. Registry tags move
separately, so the
host independently requires the complete successful publication, a successful
promotion step, and matching OCI source/revision labels on both images. A mixed
pair or a changed branch leaves the running application untouched.

## Host installation

The three standard-library Python scripts are shipped inside the backend image:

- `scripts/run_home_server_updates.py` resolves credentials and runs the cycle.
- `scripts/upstream_release_sync.py` coordinates upstream integration and CI.
- `scripts/home_server_update.py` verifies and deploys a completed publication.

Install them together from a verified backend image into
`/mnt/Pool/Services/Data/securo/updater/versions/<revision>/`. The `current` symlink
selects one complete bundle. Cron invokes Python explicitly; the private scripts
need read permission, not executable permission:

```text
*/5 * * * * /usr/bin/python3 /mnt/Pool/Services/Data/securo/updater/current/run_home_server_updates.py --config /mnt/Pool/Services/Data/securo/updater/config.json
```

The private JSON configuration has this shape. `credential_command` is an explicit
argument list for the existing local secret resolver, never a stored token:

```json
{
  "state_dir": "/mnt/Pool/Services/Data/securo/updater",
  "credential_command": ["/absolute/path/to/credential-resolver"],
  "upstream_sync": {
    "cache_dir": "/mnt/Pool/Services/Data/securo/updater/upstream-sync"
  },
  "updater": {
    "stack_dir": "/mnt/Pool/Services/Stacks/securo",
    "public_url": "https://securo.tomerh2001.com/",
    "health_timeout": 180,
    "self_update": true
  }
}
```

Keep the directory mode `0700` and configuration, state, scripts, and backups
`0600`. The orchestrator resolves the token in memory and passes it to its children
through their environment. The deployer removes those token variables from Docker
subprocess environments. Credentials never enter source control, GitHub secrets,
command arguments, deployment state, or issue bodies.

The orchestrator takes a nonblocking cycle lock. The deployer also takes its own
nonblocking lock before reading state. Docker clients inherit that descriptor so
an interrupted parent cannot leave a surviving client without a lock. The wrapper
terminates its child process group on timeout. Both mechanisms complement the
persisted deployment intent; they do not replace it.

## Deployment gates and order

Only `securo-backend`, `securo`, `securo-worker`, and `securo-beat` are managed by
this deployer. All four must already have
`com.centurylinklabs.watchtower.enable=false`. Postgres and Redis retain the existing
daily patch update policy. Bootstrap those four labels using the currently running
image versions before enabling the new schedule.

For a newer published revision, the updater:

1. Pulls the four app services, verifies the two image revisions and digests, and
   checks that the tested commit is still current `main`.
2. Creates a fresh Postgres custom-format backup, checks the archive signature and
   `pg_restore --list`, and records its size and SHA-256 in private state. It checks
   `main` again after backup, before downtime.
3. Saves a durable deployment intent containing the exact paired image digests and
   backup. It stops beat and worker, then frontend and backend.
4. Starts only backend with the verified digest and `--no-deps`. The existing
   container startup runs migrations. The updater waits for API HTTP readiness,
   a database `SELECT 1`, and Redis `PING`.
5. Starts frontend and checks its public HTTPS HTML response. It starts worker and
   checks the specific worker's Celery ping, then starts beat last.
6. Verifies all four runtime revisions. It stages all three updater scripts from
   that verified backend, checks their Python syntax, and atomically switches the
   complete bundle pointer. It records success and resolves any earlier deployment
   issue.

The digest override applies only to this rollout. The stack's normal image settings
continue to follow the fork's published `latest` tags. The updater never recreates
Postgres, Redis, or unrelated stacks, and never triggers a bank sign-in or account
refresh itself.

## Status and failures

Routine successful or unchanged checks produce no output. Private `status.json`
records the last check, upstream release, current fork commit, deployed revisions,
publication run, backup, phase, and sanitized error code. The orchestrator and
coordinator maintain their own private status alongside it.

Missing publication, mismatched image tags, and branch races wait for the next
cycle without stopping services. Preflight failures, including unavailable pulls
or backups, retry with exponential backoff from five minutes to one hour. A
failure after deployment intent is written blocks that revision from automatic
retry. An interrupted active intent is recorded as `interrupted_deployment` on the
next run, preserving its backup and blocking the same revision. A newer tested
revision can proceed after the deployment lock is available.

Deployment failures open or update one sanitized issue in the fork. It contains
the revision, phase, and fixed error code; it excludes logs, credentials, financial
records, and private backup paths. Repeated identical failures do not post repeated
updates. A later successful deployment closes the issue. If the runtime was
recovered separately, closing an earlier issue requires API, database, Redis,
frontend, worker, and complete installed bundle checks. A matching running process
alone cannot clear a previously failed rollout.

The updater never restores a database or rolls images back automatically after
migration. Retain the recorded backup and inspect the migration/application state
before manual recovery. After resolving a blocked revision, an operator may clear
its specific `failed_revisions` entry while holding the update locks, or deploy a
newer tested correction. Do not delete all status or bypass backup verification.
Create `updater/pause` to pause deployments while investigating. The coordinator
can still prepare upstream integration; disable the scheduled task to pause the
entire cycle.

## Verification

`python3 -m unittest discover -s backend/scripts/tests -p 'test_*.py'` exercises
the release coordinator, publication gate, orchestrator, and host deployment
guards. The deployment tests use fake GitHub and Docker boundaries, including
mixed image tags, moving `main`, failed backups and migrations, health failures,
interrupted rollouts, retries, issue resolution, and bundle replacement. They do
not touch running services or financial data.
