# Per-User Stop, Removal and Login Startup

## User Commands

Stopping and uninstalling this per-user application do not require administrator
approval. There are no elevation helpers or privileged service operations.

Settings contains one Stop agent action. Electron and Qt show one ordinary
confirmation explaining that the application closes until the next launch or
sign-in, and that pending activity is preserved. Cancel leaves the app running.
Only confirmation invokes `stop-agent` with an empty payload in Electron or
`bridge.requestStop()` without arguments in Qt.

Remove app is visible only when `canUninstall` is exactly `true`. Electron calls
`uninstall-agent` with an empty payload; Qt calls `bridge.requestUninstall()`.
The frontend does not open an additional confirmation. The current-user native
uninstaller owns its single confirmation and pending-data warning. Launching
that window is not proof of completed removal. While `uninstalling` is true,
the UI reports the pending operation and disables duplicate or mutating actions.
The backend polls the native child and reports `uninstall_cancelled`,
`uninstall_failed` or `uninstall_requested`. Failures do not expose filesystem
paths or private error details. Preview, health, installer and busy guards
must prevent unsafe lifecycle actions.

## Controller Contract

`AdminControl` keeps its historical name for internal compatibility. Its
`request_stop()` method returns `{authorized: true, state: 'authorized'}` without
an OS approval prompt. A concurrent request can return `busy`; callers must not
treat that as permission to exit. Status reports `scope: per_user`,
`admin_required: false` and `force_kill_protected: false`.

The controller must save final activity through the existing graceful worker
shutdown before exiting. If saving fails, it remains open and reports
`stop_pending_activity`. Stop does not delete queued events, change tracking
policy, disable login startup or remove the application. Unsynced events remain
for the next launch. Normal shutdown closes SQLite and stops the current run;
it must not be converted into an immediate supervisor restart.

The user owns the installation and its processes. This is not an
administrator-protected system service. No protection against force-quit is
claimed. Native uninstall and startup operations remain scoped to this OS user.

## Startup Registration

Integrated setup writes and reads back the stable launcher's `--autostart`
registration on fresh install, same-version setup, and upgrade:

- Windows: current-user `Software\Microsoft\Windows\CurrentVersion\Run`.
- macOS: user `Library/LaunchAgents/com.soft.tracking.v3.plist`, `RunAtLoad`.
- Linux: XDG user autostart `soft-tracking-v3.desktop`.

Unix startup files are replaced atomically. Setup fails if registration cannot
be verified. `integrate=False` performs no startup registration. Read-only
`autostart_status(executable)` reports `registered` as true, false or null
(unreadable), `scope: user_login`, and `effective: unknown`. Registration is not
a guarantee of execution: Task Manager, launchd overrides, desktop session
policy or system administration can disable it. No OS override is reset, no
startup-disabled setting is bypassed, and no system boot service is installed.
The installer launches immediately; these registrations apply at later login.
Duplicate `--autostart` launches do not request another visible window. A normal
explicit launcher invocation still opens the existing UI.

## Verification Boundary

Controller and UI fixture tests verify confirmation routing, pending/error
states and disabled controls. They do not prove native uninstall completion or
login startup on a real OS account. Native packaging, cancellation, removal and
later sign-in behavior require coordinated platform testing. Native builds
remain GitHub-only.
