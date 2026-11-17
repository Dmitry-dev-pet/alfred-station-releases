# Alfred Station — verified public releases

Public ARMv7 binaries for the existing Alfred Station Secure MCP Tunnel.
**No private source, keys, firmware, GitHub App credentials, or Station user data.**

## Publisher (proposed; NOT active until this PR is reviewed and merged)

This repository publishes its own Releases using an **ephemeral job-scoped
`GITHUB_TOKEN` with `contents: write`**. It does **not** use the broad
`dmitry-agent-hub-control` App, a new GitHub App, a PAT, Codex, or the
`github-control` self-hosted Mac runner. It runs on the standard hosted
`ubuntu-24.04` runner, which GitHub makes available without Actions-minutes
charges for public repositories.

The new `.github/workflows/publish-station-release.yml` workflow has **only**
a manual `workflow_dispatch` trigger. It rejects all actors except
`Dmitry-dev-pet`, all refs except default `main`, and any input other than
the two hardcoded, immutable release version selections. Before initiating
any publication, the owner must type `HARDWARE-VERIFIED-PUBLISH`; **this is
a manual assertion, not a substitute for real hardware evidence**.

A first job reconstructs and verifies the ELF using no write token. The
second job independently repeats that verification, then uses only its own
repository's `GITHUB_TOKEN` to publish. No arbitrary URL, commit, file
path, SHA, shell command, or source package can come from a dispatch input.

The release payload is exactly **two files**: `station-armv7` (executable)
and a derived four-line `station-armv7.manifest`. The publisher creates a
draft, uploads and SHA-256/size-verifies both assets by GitHub API readback,
and then publishes stable (`draft=false`, `prerelease=false`). If the final
GitHub publication request might have succeeded but its response was lost,
the publisher performs one read-only reconciliation; an unresolved outcome
is `PUBLICATION_UNCERTAIN` with exact release ID and tag, **never a retry**.

GitHub Releases are **not** installations on the physical speaker.

## Exact immutable candidate versions

| Tag | Stage commit | Size | SHA-256 |
| --- | --- | ---: | --- |
| `v2026.11.16` — previous installed status-only ELF, pending fresh Mac/ADB readback | `9d1526a7c0d4ed5be17782066dfef8f36a66c632` | 5,963,900 B | `71c544e083fee96487103ed60e51b92bf4844d8f2b42a3d9ff9f86464dc1e528` |
| `v2026.11.17` — corrected PR #60 release/updater candidate, NOT installed | `6b134118aeb21286e773eb87c63ecdfcf43ee6d5` | 6,357,116 B | `8b7b5b6ddc9057662f5d29125c01f8db24d16bb55eded94ac57ba666610165f0` |

The baseline contains 16 staged Git blobs; the new candidate 17. Each
stage manifest and every blob SHA have already been independently checked.
The old stage `staging/selfupdate-20261117-armv7`, SHA `e4969cf9...`,
is **superseded and must never be published**.

## Release readiness gates — do NOT publish yet

1. Review and approve this PR and read-only CI; merge only after the policy/
   security review of the new `contents: write` workflow.
2. Confirm the current speaker's *installed* binary is really the exact
   5,963,900-byte baseline SHA via the approved **read-only Mac/ADB**
   [Station #57](https://github.com/Dmitry-dev-pet/alfred-station/issues/57)
   and [TruthRail #414](https://github.com/Dmitry-dev-pet/truthrail/issues/414).
   Confirm real `/tmp` tmpfs, free RAM, UBIFS `/data` capacity and
   GitHub Release HTTPS/TLS with no changes on the speaker.
3. Before exposing `v2026.11.17` as the automatic stable latest release,
   finish the separate PR #60 integrated updater/voice/rollback hardware
   acceptance. Do not install an unverified binary or enable auto-check.
4. After those gates, the owner goes to **Actions → Publish verified Alfred
   Station release → Run workflow**, chooses `v2026.11.16` first and types
   the exact confirmation string. Download and independently verify its
   executable and manifest.
5. Only after the baseline release is independently confirmed, explicitly
   dispatch `v2026.11.17`. The publisher independently refuses this version
   unless the baseline Release is stable and both old assets have the correct
   reported digests and sizes. Verify `releases/latest/download/station-armv7.manifest`
   resolves to `v2026.11.17` after publication.

**Leave `RELEASE_AUTOCHECK=0` on Station.** A sleeping but otherwise alive
tunnel is not failure. Rollback by downloading the previous tag is
network-dependent and cannot promise recovery during an outage.

The former [`github-control` PR #285](https://github.com/Dmitry-dev-pet/github-control/pull/285)
was an alternative App-backed publisher proposal. **Do not merge or activate
both publishers**; the proposed repository-local route replaces it only
after review and validation.
