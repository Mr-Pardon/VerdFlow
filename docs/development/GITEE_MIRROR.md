# Gitee Mirror

GitHub is the canonical VerdFlow repository. https://gitee.com/mr-pardon/VerdFlow is a one-way source mirror. Make development changes and submit pull requests on GitHub; do not edit mirrored refs on Gitee.

## Setup

Create a dedicated Gitee personal access token with the necessary repository/project write permission. A dedicated automation account restricted to this repository reduces the credential's reach. Add it to the GitHub repository's Actions secrets as `GITEE_SYNC_TOKEN`. Never commit the token, place it in a remote URL or paste it into issues or logs. Rotate it when expired or no longer needed.

The Sync Gitee workflow runs after main pushes and v-prefixed tag pushes. Actions / Sync Gitee / Run workflow on main retries manually and backfills existing version tags. Only the canonical GitHub repository can run the credentialed job; no pull-request event receives the secret. Each job reads current main with full history, and concurrent jobs are serialized.

## Boundaries

The workflow uses HTTPS certificate verification and an ephemeral askpass helper. Credentials are read from the environment, never embedded in URLs, command arguments or tracked files. GitHub checkout credentials are not persisted. The job removes its temporary helper and the runner is ephemeral.

Only main and v-prefixed tags are copied. Existing target-only branches and tags are retained. There is no force push, mirror push or pruning. A divergent main or conflicting version tag blocks the operation before pushing; concurrent remote changes may cause a safe rejection. Retry after resolving discrepancies on GitHub. This is not a two-way merge service.

Release descriptions, binary attachments, issues, pull requests, stars, Topics and Pages are outside Git ref synchronization. Application downloads and the built-in updater continue using the official GitHub releases. Repository metadata is maintained separately.

## Verification

`python -m unittest discover -s .github/tests -v` tests fast-forward updates, annotated tags, repeated syncs, divergence, conflicting tags, ref scope and credential hygiene using isolated local repositories. The workflow verifies remote refs after pushing. Inspect the Sync Gitee Actions run for failures; a missing secret is an incomplete setup, not a successful synchronization.

An infrastructure-only change does not replace existing release tags or binaries and does not require bumping the application version.
