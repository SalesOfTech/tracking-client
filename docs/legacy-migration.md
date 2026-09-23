# Personalized Legacy migration (Windows pilot)

This is a user-run migration tool, not the broken Legacy updater. Download the
personalized executable from authenticated CRM for the exact Legacy installation,
then run it in that employee's Windows/RDP session **without elevation**. No company
code, employee key or migration code is typed. No second administrator approval is
requested. English, Russian, Czech and Uzbek console guidance is included.

## Authorization contract

The download proxy names the trusted binary:
`SOFT-Tracking-Migrate-{target}_{token64}.exe`. The tool reads the lowercase 64-hex
capability from frozen `sys.executable`, never a command-line secret. Browser
duplicate-download suffixes such as ` (1)` are accepted. Keep the original name.
Do not share the personalized download or screenshots of its filename. The token
is not written to the journal or printed. It is a short-lived bearer capability,
not a proof supplied by the old agent; filenames may still be visible to Windows,
the browser, security software and administrators.

The authenticated CRM download authorizes a grant pinned to one exact server
Legacy config/company/employee. At authenticated issuance, the backend can provision
an absent v3 member from the host-verified active CRM profile. Existing employee
keys are retained, including hash-only keys: no recoverable employee key is required.
Disabled membership, duplicate mappings, wildcards, expired grants and changed
mappings fail closed. Redemption directly enrolls the durable device in a server
transaction. The client never mints or rotates an employee key and does not infer
authorization from an old username.

Discovery reads only the current account's HKCU `Run/SOFTAgent` and owned Legacy
processes in the current session, then `agent_config.json` next to the one
unambiguous Legacy installation. EXE/config may be in the current profile or local
Windows Program Files roots obtained from OS/registry APIs, not environment overrides.
Program Files discovery is read-only. Other users' profiles, UNC/shared network
locations and reparse points are not accepted.
It reads `install_id.txt` from token-resolved LOCALAPPDATA/SOFT/Agent. Company ID
comes from that config; Windows username comes from the current process token SID,
and machine name from the Windows API. Inherited USERNAME is not used. The local
install ID is informational: the Legacy server has no stored secret to verify it.
The config's base_url is not used for requests; all requests use the v3 HTTPS host.

`POST /client/v3/migration_redeem` uses a separate HTTP session without Bearer auth:
`{migration_token, device_id, device_secret, company_id, username, machine, os,
install_id}`. Reply must contain `{ok:true, company_code, device_id, company_id,
company_name, user_id, user_name, expires_at}` with valid formats, matching device
and unexpired lifetime. No employee key is returned. Device
credentials are generated once by the official Client and retained in the existing
protected v3 state database for every retry. There is no pending-approval UI.

## Ordered transition

1. Validate filename target, current non-elevated user/session, local Legacy files
   and discovery boundaries. Refuse unsupported shared paths, reparse points,
   custom Legacy command arguments, unknown process ownership, conflicting profiles
   and ambiguous installations. Ignore other users and other sessions of the same
   user, even if their processes use exactly the same executable.
2. Reserve a fresh v3 workspace, or validate this tool's own migration journal
   against owner, session, root, local metadata and unchanged Legacy snapshot.
3. Redeem the personalized grant with the durable device identity. Any failure
   leaves the old agent running and its startup untouched.
4. Call `installer.install(..., integrate=False, retire_legacy=False)` to verify
   and install the official signed package. Never patch installer/bootstrap.
5. Fetch fresh device-Bearer-authenticated `/config`, require the same company/user/
   device identity and valid policy lifetime, then persist identity, company-code
   hash and policy atomically using `profiles.update_active`. Do not call key-based
   `Client.enroll`. Repeat grant/config validation on resume and refuse identity
   changes. Run `installer.installed_health` before retiring Legacy.
6. Recheck user/session, Legacy snapshot and local metadata. Register native host,
   shortcuts, the Apps uninstall entry and v3 autostart. Only then call
   `legacy_migration.replace_current_user` with the verified session ID and allowed
   executable paths. It rechecks session/owner/path before stopping each process.
7. Launch v3 and retain all Legacy binaries/configuration in place for recovery.

## Shared Program Files and RDP

The migrator's strict-session retirement never renames or deletes any Legacy
binary, including profile-local files that another session could be using. It
removes only this account's HKCU SOFTAgent startup entry and stops only matching
processes in the invoking session. HKCU is account-wide, not session-specific;
already-running processes in other sessions are preserved. The older installer
call without a session argument retains its existing behavior.

Before preparation and again before retirement, read-only checks inspect both HKLM
registry views (Run/RunOnce/Explorer policy Run), services, common Startup entries
and enabled/running scheduled task executable actions. A matching global launcher,
opaque common Startup script, nested RunOnceEx commands, permission failure or
incomplete/bounded inventory blocks automatic migration for administrator review.
No HKLM value, service, scheduled task, common Startup entry or shared file is
modified. Stopped demand-start services still block; only disabled and stopped
services are inert. The migrator does not request elevation to bypass a blocker.

This is not a guaranteed one-click migration on every RDP farm. In particular an
unrelated script in Common Startup or an unrelated populated RunOnceEx can block
the entire preflight: the tool does not interpret those commands to prove them
harmless. Ask the Windows administrator to inspect those registrations and the
farm's software-management policy, then arrange a reviewed migration path. Do not
delete unrelated startup entries, elevate the migrator, or bypass the checks just
to make it continue. Before preflight completes, no migration journal may exist;
the original Legacy startup/processes have not been changed by this tool.

These checks cover discoverable registrations, not arbitrary behavior inside an
unrelated executable/COM task handler or external management system. Farms where
such systems manage Legacy restart need administrator review before a pilot; do
not treat a clean inventory as proof about undisclosed management scripts.

## Failure and resume

`standalone-migration.json` in LOCALAPPDATA/SOFT/TrackingV3 contains no capability,
employee key or device secret. States `failed_before_switch`, `preparing` and
`rolled_back` allow a validated rerun from the same session. A transient redemption,
enrollment, policy or health failure does not require deleting the installation or
identity. Partial install directories without current.json are moved aside as
`incomplete-install-*`; device state and outboxes remain intact. No directory is
recursively deleted. A newly issued download may resume the same bound migration.

Retirement/launch exceptions disable v3 autostart and attempt to restore unchanged
HKCU startup and previously running executables in the invoking session. Rollback
never renames/restores shared or profile-local files, and never adopts a preexisting
`.legacy-disabled` sibling. Another session's running process does not suppress
restarting this session's original executable.
`recovery_required` means automatic recovery could not be verified. `switching`
left by process termination/power loss is deliberately not auto-resumed. Contact
the administrator; never delete the journal, identity, outboxes or rollback files.
Unknown/existing v3 installations are not adopted. Interrupted changes to native
host registration/shortcuts may remain; this is not a full transactional OS rollback.

## Packaging and verification boundary

Windows builds use the official `tools/release.py` freeze helper with the same
embedded setup-payload and console enabled. Output is SOFT-Tracking-Migrate.exe and
migration.json `{file,version,target,sha256}`. `tools/ci.py export` places
SOFT-Tracking-Migrate-{version}.exe and adjusted migration.json beside setup.json
in the existing target ZIP. GitHub delivery exports the versioned migrator; the
download proxy supplies the personalized filename. No additional signing key or
untrusted downloaded keyring is introduced. Other OS targets do not include it.

Tests use fake network/OS calls and temporary local databases; they do not enroll
real employees, kill live agents, run native builds or publish artifacts.
The Windows packaging smoke launches the built migrator with `--self-test`:
it verifies/installs its real signed payload in a temporary directory, exercises
direct identity acceptance and checks ordering with fixture network/OS boundaries.
All HTTP transport and real OS integration are blocked; health and runtime launch
are mocked in this isolated smoke and tested separately by the existing package
smoke. Older packages without migration.json remain valid baseline smoke inputs.
No frozen smoke was run locally without a reviewed native build. A real
Windows/RDP pilot, signed binary verification, backend grant integration and
post-launch running-agent checks remain release gates. Health-check success plus
Popen success is not proof of sustained runtime health. Process termination/power
loss during final switching requires operator recovery. Server grant expiry still
applies to retries; the tool cannot extend it locally.
