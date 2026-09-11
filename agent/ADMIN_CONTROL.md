# Cooperative Admin Stop and Login Startup

## Security Boundary

The current install is per-user. The user owns the installation, supervisor,
stop-request file and SQLite state. OS process ownership permits that user to
kill the agent, edit its files, disable login startup, or terminate the session.
An administrator authorization prompt on an application command cannot change
those permissions. `force_kill_protected` is therefore always `false`.

Preventing ordinary-user force-kill requires a separately approved architecture:
an administrator-owned Windows service, macOS daemon or Linux system service;
protected binaries and policy; authenticated, narrow IPC; and a session process
for interactive capture and visible UI. A privileged service cannot make an
ordinary user's capture process unkillable. Session loss, OS shutdown, capture
permissions, upgrades and offline delivery need explicit lifecycle policies.
No such service is installed or implied by this implementation.

The existing token-file maintenance stop used by installers/updaters is still
user-writable. Legacy UI quit paths and renderer/process exits must also be
considered by the UI owner; this module does not secure those paths.

## Controller Contract

```python
from .admin_control import AdminControl

control = AdminControl(install_root)  # one instance for the controller lifetime
snapshot = control.status()          # read-only; never opens a prompt
result = control.request_stop()      # run in the controller's background job
if result['authorized'] is True:
    controller.exit_requested = True # existing graceful shutdown, not kill
```

The result is `{authorized: bool, state: str}`. States are `authorized`,
`cancelled`, `denied`, `timed_out`, `unavailable`, `error`, and `busy`.
Every result except `authorized` leaves the application running. Concurrent
requests do not open additional prompts. No result, password, administrator
identity, grant or stop receipt is stored. A later request asks the OS again;
OS policy may reuse an administrator's existing authorization. Do not turn
`status()` or a previous success into a reusable grant.

Status reports `scope: per_user`, `admin_required: true`,
`force_kill_protected: false`, `request_pending`, `authorization` and `autostart`.
Authorization availability means the fixed helper exists, not that a desktop
authorization agent is running or that policy will approve the request.
Raw errors and helper output are not returned to the renderer. UI command
validation, graceful shutdown wiring, all quit paths and locale strings belong
to the main UI owner. No renderer-supplied argument enters an elevated command.

This API does not set worker stop flags, change tracking policy, flush or delete
queued events, disable startup, or write the maintenance stop-request file.
The existing worker shutdown must save final activity and close SQLite normally.
Unsynced events remain for the next launch. An authorized stop is for the current
session run; login startup remains registered.

## Platform Authorization

- Windows: `ShellExecuteExW` with the `runas` verb launches only the OS-owned
  `whoami.exe` from `GetSystemDirectoryW`, with no arguments and a system working
  directory. Authorization succeeds only after the returned process exits zero.
  UAC handles consent or separate administrator credentials; the agent never
  reads them. The OS prompt identifies the Windows helper, not a signed SOFT
  service. No mutable agent executable or Python module is elevated.
- macOS: fixed `/usr/bin/osascript` requests administrator privileges for the
  constant `/usr/bin/true` operation, with an explanatory SOFT Tracking prompt.
  Root ownership and non-writable ancestry of both helpers are checked.
- Linux: fixed `/usr/bin/pkexec --disable-internal-agent --user root /usr/bin/true`
  delegates authorization to polkit. There is no terminal/password fallback.
  Missing polkit or an authentication agent fails closed. Root ownership and
  non-writable ancestry are checked. Administrator-managed polkit policy remains
  authoritative; this implementation does not install an action or override it.

Unix helpers receive a minimal environment, no stdin and no caller command.
Helpers perform no installation, queue mutation, service control or process kill.
The operation timeout is 180 seconds. On Windows the synchronous OS UAC dialog
may remain open before the helper process is returned; only the helper wait is
bounded. The UI must keep tracking while waiting, not assume a deadline grants
permission. No native compilation or real elevation is part of local unit tests.

## Actual Startup Registration

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

Local tests mock every authorization API and registry operation; startup-file
tests use temporary directories. Real UAC credentials/cancellation, macOS
Authorization Services, polkit desktop behavior, login startup and native
packaging require coordinated platform testing. Native builds remain GitHub-only.
