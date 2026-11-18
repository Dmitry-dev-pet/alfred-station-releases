#!/usr/bin/env bash
# One reviewed publication. Does NOT modify Mac MCP, Station, or other releases.
set -euo pipefail
umask 077

readonly SOURCE_REPO='Dmitry-dev-pet/alfred-station'
readonly TARGET_REPO='Dmitry-dev-pet/alfred-station-releases'
readonly RUN_ID='44406396312'
readonly ARTIFACT='station-bluetooth-duck-armv7'
readonly TAG='v2026.11.18'
readonly NAME='yandex-station-chatgpt-voice.gz'
readonly GZ_SHA='62e5794e4558fa255e018468e09fb893b19b8ad715a957bc5954e872aea8d289'
readonly ELF_SHA='292e993a48d358ff17ce26908cc0c237d5e362b1710ee378166b9e65fefe6fe0'
readonly GZ_BYTES=5934905
readonly ELF_BYTES=15401432
readonly SRC_COMMIT='b9ac7f12c73ef71a3af4d4c14bb2113bb0bd76cc'

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
for cmd in gh shasum gzip wc mktemp; do command -v "$cmd" >/dev/null 2>&1 || fail "missing $cmd"; done
[[ "$(gh api user --jq '.login')" == 'Dmitry-dev-pet' ]] || fail 'wrong GitHub account'

# This is intentionally NOT an automatic build or a dynamic/tag-selected publisher.
work="$(mktemp -d)" || fail 'cannot create private working directory'
trap 'rm -rf -- "$work"' EXIT
mkdir -m 700 "$work/source" "$work/readback"
gh run download "$RUN_ID" --repo "$SOURCE_REPO" --name "$ARTIFACT" --dir "$work/source"
archive="$work/source/$NAME"
elf="$work/source/yandex-station-chatgpt-voice"
[[ -f "$archive" && ! -L "$archive" && -f "$elf" && ! -L "$elf" ]] || fail 'artifact files missing or symlinks'
[[ "$(wc -c < "$archive" | tr -d ' ')" == "$GZ_BYTES" ]] || fail 'compressed size mismatch'
[[ "$(shasum -a 256 "$archive" | awk '{print $1}')" == "$GZ_SHA" ]] || fail 'compressed SHA mismatch'
[[ "$(wc -c < "$elf" | tr -d ' ')" == "$ELF_BYTES" ]] || fail 'ELF size mismatch'
[[ "$(shasum -a 256 "$elf" | awk '{print $1}')" == "$ELF_SHA" ]] || fail 'ELF SHA mismatch'
gzip -t "$archive" || fail 'gzip integrity check failed'
[[ "$(gzip -cd "$archive" | shasum -a 256 | awk '{print $1}')" == "$ELF_SHA" ]] || fail 'uncompressed SHA mismatch'

# Fail closed on duplicate version. The candidate is deliberately a prerelease
# to avoid affecting any tunnel updater using /releases/latest.
if gh release view "$TAG" --repo "$TARGET_REPO" >/dev/null 2>&1; then
  fail 'target release already exists; no overwrite or retry'
fi

gh release create "$TAG" "$archive#$NAME" \
  --repo "$TARGET_REPO" --target main --prerelease \
  --title 'Alfred Station voice — Bluetooth ducking candidate (18 Nov 2026)' \
  --notes "Voice build from $SOURCE_REPO PR #69, source commit $SRC_COMMIT; GitHub Actions run $RUN_ID. Host race tests, vet and ARMv7 build passed. Candidate only: NOT installed, NOT physically audio-accepted, no automatic update. Gzip SHA256: $GZ_SHA; bytes: $GZ_BYTES."

# Read back the public asset after GitHub publication, not only local source.
gh release download "$TAG" --repo "$TARGET_REPO" --pattern "$NAME" --dir "$work/readback"
readback="$work/readback/$NAME"
[[ -f "$readback" && ! -L "$readback" ]] || fail 'published asset readback absent'
[[ "$(wc -c < "$readback" | tr -d ' ')" == "$GZ_BYTES" ]] || fail 'published asset size mismatch'
[[ "$(shasum -a 256 "$readback" | awk '{print $1}')" == "$GZ_SHA" ]] || fail 'published asset hash mismatch'

printf 'PUBLIC_PRERELEASE_VERIFIED\n'
printf 'url=https://github.com/%s/releases/download/%s/%s\n' "$TARGET_REPO" "$TAG" "$NAME"
printf 'sha256=%s\nbytes=%s\n' "$GZ_SHA" "$GZ_BYTES"
