#!/bin/bash
set -u
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
umask 077

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

if (( EUID == 0 || UID == 0 )); then
    printf '%s\n' 'STI_E_ROOT: Run this installer under the target user account, without sudo.' >&2
    exit 1
fi

script_dir=$(CDPATH='' cd -- "$(/usr/bin/dirname -- "$0")" && pwd -P) || fail STI_E_LOCATION
source_app="$script_dir/SOFT-Tracking-Setup.app"
[[ -d "$source_app" && ! -L "$source_app" ]] || fail STI_E_SETUP_MISSING

printf '%s\n' \
    'SOFT Tracking is not notarized by Apple. macOS cannot establish trusted developer identity for this package.' \
    'Continue only if you trust the source of this download.' \
    'This fallback copies Setup to a private temporary folder, verifies its code-signature integrity,' \
    'then removes quarantine only from that copy and its verified SOFT engine cache.' \
    'Signature integrity is not notarization.' \
    'It does not change Gatekeeper settings or install Python or Homebrew.'
printf 'Trust this download and run Setup for the current user? [y/N] '
answer=''
IFS= read -r answer || exit 1
case "$answer" in
    y|Y) ;;
    *) printf '%s\n' 'Installation cancelled.'; exit 1 ;;
esac

temp_root=${TMPDIR:-/tmp}
[[ "$temp_root" == /* && -d "$temp_root" ]] || fail STI_E_TMPDIR
temp_dir=''
# Called indirectly by the EXIT trap.
# shellcheck disable=SC2317
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$temp_dir" ]]; then
        /bin/rm -rf -- "$temp_dir" 2>/dev/null || printf '%s\n' STI_E_CLEANUP >&2
    fi
    exit "$status"
}
trap 'cleanup' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
temp_dir=$(/usr/bin/mktemp -d "${temp_root%/}/SOFTTrackingSetup.XXXXXXXX") || fail STI_E_TEMP
[[ -d "$temp_dir" && ! -L "$temp_dir" && -O "$temp_dir" ]] || fail STI_E_TEMP

copied_app="$temp_dir/SOFT-Tracking-Setup.app"
/usr/bin/ditto "$source_app" "$copied_app" >/dev/null 2>&1 || fail STI_E_COPY
[[ -d "$copied_app" && ! -L "$copied_app" && -O "$copied_app" ]] || fail STI_E_COPY
for component in "$copied_app/Contents" "$copied_app/Contents/MacOS"; do
    [[ -d "$component" && ! -L "$component" ]] || fail STI_E_LAYOUT
done
executable="$copied_app/Contents/MacOS/SOFT-Tracking-Setup"
[[ -f "$executable" && -x "$executable" && ! -L "$executable" ]] || fail STI_E_LAYOUT
/usr/bin/codesign --verify --deep --strict "$copied_app" >/dev/null 2>&1 || fail STI_E_SIGNATURE
# Do not follow copied framework/resource links outside this private app.
/usr/bin/xattr -dr -s com.apple.quarantine "$copied_app" >/dev/null 2>&1 || fail STI_E_QUARANTINE
"$executable" --trust-setup-engine >/dev/null || fail STI_E_ENGINE_TRUST

# Stay attached so the copied payload lives until Setup exits, and retain its status.
"$executable" "$@"
status=$?
exit "$status"
