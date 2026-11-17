# Alfred Station ARMv7 binary release contract

This public repository is an **artifact-only** distribution channel. The private source and device configuration remain in `Dmitry-dev-pet/alfred-station`.

A published release must use:
- an immutable version tag matching `vX.Y.Z` (optional conservative prerelease suffix);
- exactly one executable binary asset named `station-armv7`;
- a declared size in bytes and **SHA-256** matching the exact uploaded bytes;
- Linux **ARM32/ARMv7 EABI5**, static ELF, tested with the pinned Go compiler in ChatGPT Linux and offline QEMU;
- provenance: private source PR/commit and a sanitized test receipt, without publishing account tokens, keys, network settings, voice sessions or other private source.

The on-device persistent **single** MCP `tunnel-client` is the intended consumer. In the first release the command `station_update_prepare` must stage the candidate, verify its digest/architecture, and only then `station_update_activate` may replace its own binary without a backup. The operating system and existing voice app are out of scope; manual ADB recovery remains available to the owner.

**Status as of 2026-11-17: NO releases published, no device self-update performed.** This document does not grant an upload credential or authorize publication. GitHub Release creation and upload require a separate reviewed owner-controlled publishing route in [github-control #284](https://github.com/Dmitry-dev-pet/github-control/issues/284). The current private source-only candidate is [alfred-station PR #55](https://github.com/Dmitry-dev-pet/alfred-station/pull/55).

Never publish data directly from a general issue body, arbitrary URL, unverified artifact or private repo without a bounded pin and publisher permission check. No GitHub Actions compilation is part of this design.
