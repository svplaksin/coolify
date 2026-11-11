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
- Aliases must not start, restart, stop, or delete the canonical Sentinel. Both `StopSentinel` and the direct cleanup in `DeleteServer` are guarded.
- The canonical owner continues to manage the shared `coolify-sentinel`; its lock key is stable across aliases.
- A healthy owner's heartbeat can suppress redundant SSH/container checks for an alias without resources; unreachable aliases and aliases with resources still receive required fallback checks.
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

**Phase B — explicit ownership:** Recheck that ID `0` is `localhost` and ID `1` is `Doctor VPS`, plus UUIDs and the physical Docker Engine identity. In a reviewed transaction, set **only** `servers.id=0` to `sentinel_owner_server_id=1`; leave ID `1` canonical (`NULL`). Verify the resulting mapping and row counts. Never guess the IDs in another installation.

**Phase C — observation:** Verify `Doctor VPS` continues receiving authenticated Sentinel push, the actual `coolify-sentinel` container remains healthy, no `kill/destroy/create` churn occurs, and independent server SSH/recovery paths remain functional. Check Doctimebot, Virosarain, proxy, Redis, PostgreSQL and application endpoints.

**Phase D — follow-up:** Review the local server's user-facing `Out of sync` warning. This code change prevents redundant ownership actions but does not yet make the alias appear synchronized in every UI view; avoid interpreting the warning alone as physical Sentinel failure.

## Stop conditions and rollback notes

Stop immediately on migration errors, failed Coolify health, proxy/application outage, changes in physical host identity, Sentinel push/authentication errors, or unexpected container destruction.

- Do not run `docker prune`, remove volumes, restart the entire Docker daemon, or delete the `localhost` logical record as a workaround.
- Keep the old Coolify image and complete pre-deployment database/configuration backups. Some Coolify migrations cannot safely be reversed by changing an image tag alone.
- If an additive alias mapping causes problems while the patched code remains healthy, a separately approved reversible mapping change can unlink the alias; this returns the **original competing-owner behavior**, so it is a temporary mitigation, not a final fix.
- If the new Coolify image or migration is faulty, use the rehearsal-tested coordinated application/database recovery plan. Never assume that replacing the image alone is a valid rollback.
- Retain detailed before/after health and Docker event evidence, but never publish credentials or token-bearing environment files.

## Testing evidence

CI runs backend regression groups for ownership, locking, Sentinel job recovery, reachability, settings, restore-default UI, navbar UI, and a frontend production build. The alias deletion regression was demonstrated to fail before the guard and pass after it; independent server deletion remains covered.

**Known outstanding:** independent `backend-test-critic` review unavailable in the current environment; no separate staging deployment or database restore rehearsal has been performed for the custom Coolify image.
