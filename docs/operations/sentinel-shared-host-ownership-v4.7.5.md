# Sentinel shared-host ownership — Coolify v4.7.5 rollout

## Scope and status

**Implementation branch:** `fix/sentinel-shared-host-ownership-v4.7.5` based on upstream Coolify v4.7.5.

**Deployment status: NO-GO until separately approved.** This runbook does not authorize changes to the VPS, Coolify, Docker, PostgreSQL, or production applications.

The root issue is two distinct Coolify server records controlling one physical Docker Engine, while a single container name (`coolify-sentinel`) and its single push token belong to only one logical server. An independent per-server lock does not protect the common Docker host.

Observed topology (verify again before rollout):
- `localhost`: logical Server ID `0`, no production resources.
- `Doctor VPS`: logical Server ID `1`, production workloads and the healthy Sentinel heartbeat.
- Both SSH destinations previously reached the **same** Docker daemon.

## Changes and safety invariants

- Add nullable `servers.sentinel_owner_server_id`; `NULL` retains independent ownership for all unconfigured servers.
- An explicit owner link resolves to one canonical logical record; invalid links, transferred owners, and ownership cycles fail closed.
- Aliases must not start, restart, stop, or delete the canonical Sentinel. Stop and delete paths refresh ownership after acquiring the shared Sentinel lock and before running `docker rm`.
- The canonical owner continues to manage the shared `coolify-sentinel`; its lock key is stable across aliases. A restart passes its already-held lock into the stop path, avoiding a nested-lock deadlock.
- A healthy owner's heartbeat can suppress redundant SSH/container checks only for an alias without resources. Aliases with applications, databases, services, or proxy retain independent SSH and container checks.
- The mapping is never inferred from matching IP addresses or DNS names.
- The mapping must be explicitly configured **after** the migration and code deployment. Merely deploying the branch will **not** resolve the production conflict.

## Pre-deployment gates (all required)

1. Independent test/code critic or reviewer has examined the final diff. A green CI run does not replace an independent review.
2. All backend regressions, PHP lint, and frontend build pass on the exact final commit.
3. Produce a custom Coolify image from the exact reviewed commit. Publish under a **unique immutable tag and digest** and independently verify the running image can be pinned to that digest. Do not accidentally substitute `latest` or overwrite an official `4.7.5` tag.
4. Rehearse startup, schema migration, Sentinel owner mapping, image rollback, and database restoration in an isolated environment.
5. Have a fresh, verified off-VPS backup of the Coolify database plus `/data/coolify/source/.env`, the instance configuration, and the effective Compose files. Preserve the `APP_KEY` securely. Existing PostgreSQL application backups are additional protection, not a full backup of Docker volumes.
6. Confirm both logical server records still target the **same physical Docker daemon**; check live IDs, UUIDs, current resource ownership, Sentinel push destination, and latest health.
7. Confirm Docker disk capacity, absence of active deployments, and a maintenance window with operator access to the VPS.
8. Obtain a separate explicit `GO` for the actual production change. The current project authorization does **not** include deployment.

## Staged rollout (requires separate GO)

**Phase A — instance code/image:** Deploy the reviewed digest through a proven Coolify custom-image procedure; do not mutate application containers. Verify the application is healthy and the new migration has applied. Validate no unrelated schema changes and no unexpected queue errors. Do not assume the official auto-updater will preserve the custom image.

**Phase B — explicit ownership:** Recheck that ID `0` is `localhost` and ID `1` is `Doctor VPS`, plus UUIDs and the physical Docker Engine identity. Confirm all web and queue workers run the reviewed image; stop the rollout if an old worker remains. Temporarily prevent new Sentinel start/stop/delete work from being submitted, wait for existing Sentinel jobs to finish, and verify no ownership lock is held. In a reviewed transaction, leave `servers.id=0` unchanged with `sentinel_owner_server_id=NULL` and set **only** `servers.id=1` to `sentinel_owner_server_id=0`. Reject the transaction unless the verified Doctor VPS row is the only updated row and the localhost row remains unchanged. Verify the resulting mapping and row counts before resuming jobs. Never guess the IDs in another installation. Do not run a manual `docker rm` during the handover.

**Phase C — observation:** Verify `localhost` receives the authenticated Sentinel heartbeat, the actual `coolify-sentinel` container remains healthy, and no `kill/destroy/create` churn occurs. Confirm Doctor VPS receives independent SSH and container checks and that its applications, databases, services, proxy, deployments, and backups remain functional. Check Doctimebot, Virosarain, Redis, PostgreSQL and application endpoints.

**Phase D — follow-up:** Review Doctor VPS for a user-facing `Out of sync` warning. This code change prevents alias ownership actions but does not make every alias UI view consume the localhost heartbeat; do not interpret that warning alone as physical Sentinel failure. Record any Doctor-specific metrics or traffic-analytics differences for separate review instead of changing localhost settings during this rollout.

## Stop conditions and rollback notes

Stop immediately on migration errors, failed Coolify health, proxy/application outage, changes in physical host identity, Sentinel push/authentication errors, or unexpected container destruction.

- Do not run `docker prune`, remove volumes, restart the entire Docker daemon, or delete the `localhost` logical record as a workaround.
- Keep the old Coolify image and complete pre-deployment database/configuration backups. Some Coolify migrations cannot safely be reversed by changing an image tag alone.
- If the Doctor VPS alias mapping causes problems while the patched code remains healthy, a separately approved change can restore only Doctor VPS `sentinel_owner_server_id` to `NULL`; localhost must remain unchanged. This returns the **original competing-owner behavior**, so it is a temporary mitigation, not a final fix.
- Do not automatically run the migration `down()` during rollback. Removing the column discards the ownership mapping and requires a separately rehearsed database recovery plan.
- If the new Coolify image or migration is faulty, use the rehearsal-tested coordinated application/database recovery plan. Never assume that replacing the image alone is a valid rollback.
- Retain detailed before/after health and Docker event evidence, but never publish credentials or token-bearing environment files.

## Testing evidence

CI runs backend regression groups for ownership, locking, Sentinel job recovery, reachability, settings, restore-default UI, navbar UI, and a frontend production build. The regressions cover stale action/job models, ownership changes after a lock is acquired, alias deletion after model hydration, independent owner stop/delete, and alias SSH fallback.

The independent `backend-test-critic` returned `PASS` for the final tests-first regression design after two revision rounds. No separate staging deployment or database restore rehearsal has been performed for the custom Coolify image.
