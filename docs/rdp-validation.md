# Windows RDP replacement and acceptance checks

Status: source changes tested locally; not a published release or a client-farm verification.

## Scope

The collector runs in the employee's Windows account, not in Session 0 or an
unrelated administrator account. WTS connection state is queried for the collector
process's session, not the active console. Disconnected sessions and secure/locked
desktops are excluded. The foreground process must belong to the same session.
Failed input/session queries pause collection instead of treating failure as input.
Idle time uses unsigned DWORD arithmetic, including counter wrap. The default idle
grace is 30 seconds. Missing polls, suspension and clock jumps close the previous
interval at its last successful observation rather than spanning the gap.

## Legacy replacement

The integrated Windows installer detects known Legacy executable names and the
current account's HKCU Run value SOFTAgent. After the new package is installed it
removes that startup entry and forcibly terminates matching processes owned by
the current account. This intentionally discards the old in-memory queue, as
authorized for this migration. It does not terminate other users' agents.

Per-user executables are renamed with the suffix .legacy-disabled for rollback.
Configuration, logs and install identifiers are retained. Shared paths, junctions,
unrecognized startup entries and other profiles are not deleted. Shared binaries
can remain on disk because other users may still need them; their current-user
startup registration is removed. Errors are not reported as successful replacement.
The local installation directory contains legacy-migration.json with the result.
This report contains local paths/account metadata and must not be published.

Run setup separately in the employee account on each relevant farm host. Running
setup under a separate administrator account does not migrate the employee.
Do not deploy farm-wide until the pilot passes. No browser extension is removed
by this migration; the desktop application collector is the scope of these checks.

## Before replacement

Obtain approved remote access through the client's secure channel, the target
host list, an administrator contact, and permission for a short interrupted session.
Do not request passwords in an ordinary support ticket.

Record the old executable version and SHA256, host uptime, employee session ID,
connected/disconnected state, process owner and duplicate process count. Preserve
the old log and a dashboard export with timezone and exact interval. Record no
employee keys in the diagnostic bundle. Check that the binary corresponds to the
Legacy source where GetTickCount has an implicit signed return type.

## Pilot matrix

Record wall-clock boundaries and expected duration for every step:

1. Foreground LBIS with real keyboard/mouse use for two minutes: approximately
   two minutes recorded, within poll and idle-grace bounds.
2. Switch to an untracked program: LBIS interval ends at the next poll.
3. Leave LBIS foreground without input for two minutes: no new time after the
   configured 30-second grace. Foreground presence alone is not human activity.
4. Lock Windows for two minutes: no LBIS time while locked.
5. Disconnect RDP without logging off for two minutes: no LBIS time while away.
6. Reconnect: a new interval starts; there is no bridge across the disconnection.
7. Connect a second employee: their activity must not extend the first employee's
   interval, and migration must not terminate their process or remove their startup.
8. Suspend polling or interrupt connectivity: missing observation time is not
   counted; already observed events survive a network outage in the v3 outbox.
9. Log out/in: only the new desktop agent starts for the migrated account.
10. Compare new-agent events with server receipts and dashboard union durations.
    A dashboard rendering check is separate from agent unit tests.

The source bug has an executable regression test for uptime of 25 days and a
10-minute idle period. This establishes the bug, not that it caused a particular
historical client session. Confirm client uptime/binary and reproduce the old
behavior before attributing the reported incident conclusively. Do not change the
RDP server's clock or reboot it just to force a counter test.

## Rollback

Use administrator-approved shutdown of the new agent and disable its user startup.
Consult legacy-migration.json; restore only the recorded .legacy-disabled binary
to its original absent path and restore the recorded SOFTAgent Run value in that
same user's HKCU. Never overwrite an existing file. Keep new-agent pending data.
The intentionally discarded old in-memory events cannot be recovered.
