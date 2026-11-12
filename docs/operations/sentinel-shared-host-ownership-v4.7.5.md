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

**Phase B — explicit ownership:** Recheck that ID `0` is `localhost` and ID `1` is `Doctor VPS`, plus UUIDs and the physical Docker Engine identity. Confirm all web and queue workers run the reviewed image; stop the rollout if an old worker remains. Temporarily prevent new Sentinel start/stop/delete work from being submitted, wait for existing Sentinel jobs to finish, and verify no ownership lock is held. In a reviewed transaction, set **only** `servers.id=0` to `sentinel_owner_server_id=1`; leave ID `1` canonical (`NULL`). Verify the resulting mapping and row counts before resuming jobs. Never guess the IDs in another installation. Do not run a manual `docker rm` during the handover.

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

CI runs backend regression groups for ownership, locking, Sentinel job recovery, reachability, settings, restore-default UI, navbar UI, and a frontend production build. The regressions cover stale action/job models, ownership changes after a lock is acquired, alias deletion after model hydration, independent owner stop/delete, and alias SSH fallback.

The independent `backend-test-critic` returned `PASS` for the final tests-first regression design after two revision rounds. No separate staging deployment or database restore rehearsal has been performed for the custom Coolify image.

## Custom image CI handoff (preparation only)

The proposed build and publish workflows are intentionally manual-only. GitHub
can dispatch them only after both workflow files exist on the fork's default
branch. This feature-branch commit therefore prepares and validates CI code but
does not authorize or start a build or publication.

After a separate explicit GO, transfer the reviewed CI-only workflow commit to
the default branch without changing the pinned source revision, version, image
name, or verification steps. Review the resulting default-branch diff, then
dispatch only the build workflow from that exact default-branch commit. Do not
add a `push` publication trigger as a workaround.

The build workflow must produce exactly two OCI artifacts (`image-amd64` and
`image-arm64`) and verify the platform plus
`org.opencontainers.image.revision` and `org.opencontainers.image.version` for
each architecture. It has no package-write permission and cannot publish.

After independent review of that successful build, a separate explicit GO is
required to dispatch the publish workflow with the reviewed build run ID and
build run attempt. The publish workflow requires the build to come from `main`
at the same reviewed workflow-code SHA as the publish workflow. This
workflow-code SHA is distinct from the pinned image source SHA `bd04cd1...`,
which is verified from the OCI revision label.

The publish workflow downloads exactly two OCI artifacts and repeats all
platform and label checks. Its tag includes the build run ID, publication run
ID, and publication run attempt. Publication fails closed unless the registry
explicitly reports that exact tag as missing; network, authentication, and
unexpected registry failures block publication. The digest is read from the
real `containerimage.descriptor.digest` metadata shape and must equal the
registry-reported manifest digest. The final manifest is accepted only when it
contains `linux/amd64` and `linux/arm64` and both images retain the reviewed
labels.

Image build, GHCR publication, custom-image installation, database changes,
and production rollout remain separate approval gates.

### Backup gate for a future rehearsal or rollout

Before any backup, identify the live Coolify Compose/configuration paths with
read-only inspection and validate every required source path individually.
Treat a missing database dump, Compose file, instance configuration, `.env`, or
securely preserved `APP_KEY` as a hard failure. Do not suppress archive errors.
After creating the archive under a separate approval, list and verify its
contents and copy it off the VPS before making state changes. A backup command
must use the verified live paths; this runbook does not assume them.
