#!/bin/bash
set -u
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
umask 077

fail() {
    printf '%s\n' "$1" >&2
    /usr/bin/logger -t SOFTTrackingPackage -- "$1"
    exit 1
}

(( EUID >= 501 && UID >= 501 )) || fail STP_E_USER
[[ $# == 2 ]] || fail STP_E_ARGUMENTS
userdir=$1
label=$2
[[ "$label" =~ ^com\.soft\.tracking\.setup\.$UID\.[a-zA-Z0-9]{6}$ ]] || fail STP_E_LABEL
validated=0
cleanup() {
    local status=$?
    trap - EXIT
    trap '' HUP INT TERM
    if [[ "$validated" == 1 ]]; then
        /bin/rm -rf -- "$userdir" >/dev/null 2>&1 \
            || { printf '%s\n' STP_E_USER_CLEANUP >&2; status=1; }
    fi
    /bin/launchctl remove "$label" >/dev/null 2>&1 \
        || { printf '%s\n' STP_E_JOB_CLEANUP >&2; status=1; }
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
temp_root=$(/usr/bin/getconf DARWIN_USER_TEMP_DIR 2>/dev/null) || fail STP_E_USER_TEMP
[[ "$temp_root" == /* && -d "$temp_root" ]] || fail STP_E_USER_TEMP
temp_root=$(CDPATH='' cd -- "$temp_root" && pwd -P) || fail STP_E_USER_TEMP
[[ -O "$temp_root" && "${userdir%/*}" == "$temp_root" \
    && "${userdir##*/}" =~ ^SOFTTrackingGUI\.[a-zA-Z0-9]{8}$ ]] || fail STP_E_USER_PATH
[[ -d "$userdir" && ! -L "$userdir" && -O "$userdir" ]] || fail STP_E_USER_PATH
[[ $(/usr/bin/stat -f '%Lp' "$userdir" 2>/dev/null) == 700 ]] || fail STP_E_USER_PATH
validated=1
export TMPDIR="$temp_root/"
export SOFT_TRACKING_HEALTH="$userdir/setup-ready.json"
executable="$userdir/SOFT-Tracking-Setup.app/Contents/MacOS/SOFT-Tracking-Setup"
[[ -f "$executable" && -x "$executable" && ! -L "$executable" ]] || fail STP_E_LAYOUT
# Stay attached to the native wrapper until the real installer window closes.
# Give setup its own process group so launchd job removal cannot kill an agent
# started by the final Install/Open action after setup has exited.
set -m
/usr/bin/logger -t SOFTTrackingPackage -- STP_GUI_START
"$executable" --prepared-setup-launch || fail STP_E_SETUP
/usr/bin/logger -t SOFTTrackingPackage -- STP_GUI_EXIT
