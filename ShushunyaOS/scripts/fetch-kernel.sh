#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
LOCK_FILE="$PROJECT_ROOT/kernel/source.lock.json"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

for command_name in curl gpg python3 sha256sum tar xz awk grep cmp flock find id mkdir chmod mv cp rm cat wc; do
  require_command "$command_name"
done

[[ -r "$LOCK_FILE" ]] || die "source lock is not readable: $LOCK_FILE"

mapfile -t lock_values < <(python3 - "$LOCK_FILE" <<'PY'
import json
import re
import sys
from urllib.parse import urlparse

def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"duplicate JSON key: {key}")
        result[key] = value
    return result

with open(sys.argv[1], encoding="utf-8") as stream:
    lock = json.load(stream, object_pairs_hook=reject_duplicate_keys)

expected_top = {
    "schema", "selected_at", "channel", "series", "version", "tag",
    "projected_eol", "source_date_epoch", "tarball", "signature",
    "key_lookup", "allowed_primary_signer_fingerprints",
}
if set(lock) != expected_top:
    raise SystemExit("source lock has missing or unexpected top-level keys")
if lock["schema"] != 1 or lock["channel"] != "upstream-longterm":
    raise SystemExit("unsupported source lock schema or channel")
if not isinstance(lock["source_date_epoch"], int) or isinstance(lock["source_date_epoch"], bool):
    raise SystemExit("source_date_epoch must be an integer")
if lock["source_date_epoch"] <= 0:
    raise SystemExit("source_date_epoch must be positive")
if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", lock["selected_at"]):
    raise SystemExit("selected_at must be an ISO date")
if not re.fullmatch(r"20\d{2}-\d{2}", lock["projected_eol"]):
    raise SystemExit("projected_eol must be YYYY-MM")
if not re.fullmatch(r"[1-9]\d*\.[0-9]+", lock["series"]):
    raise SystemExit("invalid kernel series")
if not re.fullmatch(re.escape(lock["series"]) + r"\.[0-9]+", lock["version"]):
    raise SystemExit("kernel version does not belong to the selected series")
if lock["tag"] != "v" + lock["version"]:
    raise SystemExit("kernel tag does not match version")

for field in ("tarball", "signature"):
    if not isinstance(lock[field], dict) or set(lock[field]) != {"url", "sha256"}:
        raise SystemExit(f"invalid {field} object")
    if not re.fullmatch(r"[0-9a-f]{64}", lock[field]["sha256"]):
        raise SystemExit(f"invalid {field} SHA-256")

version = lock["version"]
expected_tarball = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{version}.tar.xz"
expected_signature = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{version}.tar.sign"
if lock["tarball"]["url"] != expected_tarball:
    raise SystemExit("tarball URL is not the pinned kernel.org URL")
if lock["signature"]["url"] != expected_signature:
    raise SystemExit("signature URL is not the pinned kernel.org URL")
for url in (lock["tarball"]["url"], lock["signature"]["url"]):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "cdn.kernel.org":
        raise SystemExit("source URLs must use HTTPS on cdn.kernel.org")

if not isinstance(lock["key_lookup"], list) or not lock["key_lookup"]:
    raise SystemExit("key_lookup must be a non-empty list")
for identity in lock["key_lookup"]:
    if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9._-]+@kernel\.org", identity):
        raise SystemExit("invalid kernel.org key identity")
if not isinstance(lock["allowed_primary_signer_fingerprints"], list) or not lock["allowed_primary_signer_fingerprints"]:
    raise SystemExit("allowed signer fingerprints must be a non-empty list")
for fingerprint in lock["allowed_primary_signer_fingerprints"]:
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[A-F0-9]{40}|[A-F0-9]{64}", fingerprint):
        raise SystemExit("invalid signer fingerprint")

print(lock["series"])
print(lock["version"])
print(lock["tag"])
print(lock["source_date_epoch"])
print(lock["tarball"]["url"])
print(lock["tarball"]["sha256"])
print(lock["signature"]["url"])
print(lock["signature"]["sha256"])
print(",".join(lock["key_lookup"]))
print(",".join(lock["allowed_primary_signer_fingerprints"]))
PY
)

[[ ${#lock_values[@]} -eq 10 ]] || die "source lock has an unexpected schema"

KERNEL_SERIES="${lock_values[0]}"
KERNEL_VERSION="${lock_values[1]}"
KERNEL_TAG="${lock_values[2]}"
SOURCE_DATE_EPOCH="${lock_values[3]}"
TARBALL_URL="${lock_values[4]}"
TARBALL_SHA256="${lock_values[5]}"
SIGNATURE_URL="${lock_values[6]}"
SIGNATURE_SHA256="${lock_values[7]}"
IFS=',' read -r -a KEY_LOOKUP <<< "${lock_values[8]}"
IFS=',' read -r -a ALLOWED_SIGNERS <<< "${lock_values[9]}"

CACHE_DIR="$PROJECT_ROOT/kernel/cache"
SOURCE_PARENT="$PROJECT_ROOT/kernel/source"
SOURCE_DIR="$SOURCE_PARENT/linux-$KERNEL_VERSION"
GNUPG_HOME="$PROJECT_ROOT/runtime/run/kernel-gnupg-$(id -u)"
TARBALL="$CACHE_DIR/linux-$KERNEL_VERSION.tar.xz"
SIGNATURE="$CACHE_DIR/linux-$KERNEL_VERSION.tar.sign"
RUN_DIR="$PROJECT_ROOT/runtime/run"

umask 0027
mkdir -p "$CACHE_DIR" "$SOURCE_PARENT" "$GNUPG_HOME" "$RUN_DIR"
chmod 0750 "$CACHE_DIR" "$SOURCE_PARENT"
chmod 0700 "$GNUPG_HOME"

exec 9>"$RUN_DIR/kernel-fetch-$KERNEL_VERSION.lock"
flock -n 9 || die "another kernel fetch is already running for $KERNEL_VERSION"

TEMP_FILES=()
cleanup_temporary_files() {
  local path
  for path in "${TEMP_FILES[@]}"; do
    if [[ -f "$path" ]]; then
      rm -f -- "$path"
    fi
  done
}
trap cleanup_temporary_files EXIT

verify_sha256() {
  local path="$1"
  local expected="$2"
  local actual
  actual="$(sha256sum -- "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || die "SHA-256 mismatch for $path: $actual"
}

download_locked_file() {
  local url="$1"
  local destination="$2"
  local expected="$3"

  if [[ -e "$destination" ]]; then
    [[ -f "$destination" ]] || die "cache path is not a regular file: $destination"
    verify_sha256 "$destination" "$expected"
    printf 'Verified cached file: %s\n' "$destination"
    return
  fi

  local partial="$destination.partial.$$"
  [[ ! -e "$partial" ]] || die "partial download already exists: $partial"
  TEMP_FILES+=("$partial")
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
    --output "$partial" --url "$url"
  verify_sha256 "$partial" "$expected"
  chmod 0640 "$partial"
  mv -T -- "$partial" "$destination"
  printf 'Downloaded and checksummed: %s\n' "$destination"
}

download_locked_file "$TARBALL_URL" "$TARBALL" "$TARBALL_SHA256"
download_locked_file "$SIGNATURE_URL" "$SIGNATURE" "$SIGNATURE_SHA256"
chmod 0640 "$TARBALL" "$SIGNATURE"

for identity in "${KEY_LOOKUP[@]}"; do
  gpg --homedir "$GNUPG_HOME" --batch --auto-key-locate clear,wkd \
    --locate-keys "$identity" >/dev/null 2>&1 || die "cannot import kernel.org key: $identity"
done

imported_fingerprints="$(
  gpg --homedir "$GNUPG_HOME" --batch --with-colons --fingerprint "${KEY_LOOKUP[@]}" 2>/dev/null \
    | awk -F: '$1 == "fpr" { print toupper($10) }'
)"

for expected_fingerprint in "${ALLOWED_SIGNERS[@]}"; do
  grep -Fxq -- "$expected_fingerprint" <<< "$imported_fingerprints" \
    || die "expected kernel.org key fingerprint was not imported: $expected_fingerprint"
done

STATUS_FILE="$GNUPG_HOME/verify-$KERNEL_VERSION.status.$$"
GPG_LOG_FILE="$GNUPG_HOME/verify-$KERNEL_VERSION.log.$$"
TEMP_FILES+=("$STATUS_FILE" "$GPG_LOG_FILE")
: > "$STATUS_FILE"
: > "$GPG_LOG_FILE"
chmod 0600 "$STATUS_FILE" "$GPG_LOG_FILE"

set +e
xz -cd -- "$TARBALL" \
  | gpg --homedir "$GNUPG_HOME" --batch --status-fd 3 \
      --verify "$SIGNATURE" - 3>"$STATUS_FILE" 2>"$GPG_LOG_FILE"
verification_rc=$?
set -e
cat "$GPG_LOG_FILE"
cat "$STATUS_FILE"
[[ $verification_rc -eq 0 ]] || die "OpenPGP verification failed for $KERNEL_TAG"

if grep -Eq '^\[GNUPG:\] (BADSIG|ERRSIG|REVKEYSIG|EXPKEYSIG|EXPSIG|KEYEXPIRED|SIGEXPIRED)( |$)' "$STATUS_FILE"; then
  die "OpenPGP reported a bad, revoked, or expired signature/key"
fi

valid_signature="$(
  awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" { print toupper($3) " " toupper($12) }' \
    "$STATUS_FILE"
)"
[[ -n "$valid_signature" ]] || die "gpg did not report a VALIDSIG record"
[[ "$(wc -l <<< "$valid_signature")" -eq 1 ]] || die "expected exactly one VALIDSIG record"
read -r signing_fingerprint primary_fingerprint <<< "$valid_signature"
[[ -n "$primary_fingerprint" ]] || primary_fingerprint="$signing_fingerprint"

signer_allowed=0
for allowed_fingerprint in "${ALLOWED_SIGNERS[@]}"; do
  if [[ "$primary_fingerprint" == "$allowed_fingerprint" ]]; then
    signer_allowed=1
    break
  fi
done
[[ $signer_allowed -eq 1 ]] || die "valid signature came from an unapproved primary key: $primary_fingerprint"
printf 'Verified signer fingerprint: %s\n' "$primary_fingerprint"

python3 - "$TARBALL" "$KERNEL_VERSION" <<'PY'
import posixpath
import sys
import tarfile
from pathlib import PurePosixPath

archive_path, version = sys.argv[1:]
root = f"linux-{version}"
prefix = root + "/"

with tarfile.open(archive_path, mode="r:xz") as archive:
    for member in archive:
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"ERROR: unsafe path in signed archive: {member.name}")
        if name != root and not name.startswith(prefix):
            raise SystemExit(f"ERROR: unexpected top-level path in archive: {member.name}")
        if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            raise SystemExit(f"ERROR: special file is not allowed in source archive: {member.name}")
        if member.issym():
            if PurePosixPath(member.linkname).is_absolute():
                raise SystemExit(f"ERROR: absolute symlink in source archive: {member.name}")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
            if resolved != root and not resolved.startswith(prefix):
                raise SystemExit(f"ERROR: escaping symlink in source archive: {member.name}")
        if member.islnk():
            target = posixpath.normpath(member.linkname)
            if target != root and not target.startswith(prefix):
                raise SystemExit(f"ERROR: escaping hardlink in source archive: {member.name}")

print(f"Verified archive member policy for {root}")
PY

if [[ -e "$SOURCE_DIR" ]]; then
  [[ -d "$SOURCE_DIR" ]] || die "source path is not a directory: $SOURCE_DIR"
  cmp -s "$LOCK_FILE" "$SOURCE_DIR/.shushunya-source-lock.json" \
    || die "existing source was created from a different lock file"
  printf 'Found read-only reference source for the current lock: %s\n' "$SOURCE_DIR"
  exit 0
fi

STAGING_DIR="$SOURCE_PARENT/linux-$KERNEL_VERSION.partial.$$"
[[ ! -e "$STAGING_DIR" ]] || die "staging path already exists: $STAGING_DIR"
mkdir -m 0700 "$STAGING_DIR"
tar --extract --xz --file "$TARBALL" --directory "$STAGING_DIR" \
  --strip-components=1 --no-same-owner --no-same-permissions
cp -- "$LOCK_FILE" "$STAGING_DIR/.shushunya-source-lock.json"

make_version="$(awk -F' = ' '
  $1 == "VERSION" { major=$2 }
  $1 == "PATCHLEVEL" { minor=$2 }
  $1 == "SUBLEVEL" { patch=$2 }
  END { print major "." minor "." patch }
' "$STAGING_DIR/Makefile")"
[[ "$make_version" == "$KERNEL_VERSION" ]] || die "extracted Makefile reports $make_version, expected $KERNEL_VERSION"

find "$STAGING_DIR" -type d -exec chmod 0550 {} +
find "$STAGING_DIR" -type f -perm /111 -exec chmod 0550 {} +
find "$STAGING_DIR" -type f ! -perm /111 -exec chmod 0440 {} +
mv -T -- "$STAGING_DIR" "$SOURCE_DIR"
printf 'Extracted immutable Linux %s source: %s\n' "$KERNEL_VERSION" "$SOURCE_DIR"
printf 'SOURCE_DATE_EPOCH=%s\n' "$SOURCE_DATE_EPOCH"
