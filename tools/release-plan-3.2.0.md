# Acceptance and Stable Publication

The separate `.github/workflows/accept-release.yml` now implements this path.
The candidate build workflow remains candidate-only. No build, acceptance,
publication, environment configuration or secret operation is performed locally.
This document retains its original filename; the contract applies to the selected
stable workflow `version` input (`ACCEPTANCE_VERSION`), including `3.5.0`.

## Operator Contract

- Finish all eight candidate build jobs successfully with the selected payload version.
- Commit the acceptance harness separately; rebuilding the candidate is unnecessary.
- Dispatch `accept-release.yml` from `main` with the matching `version` and
  `candidate_tag` exactly `candidate-<version>-<run_id>-<run_attempt>`.
- The dispatch actor and rerun actor must have repository admin or maintain access.
- Environment `tracking-release-acceptance` must have
  `deployment_branch_policy={"protected_branches":false,"custom_branch_policies":true}`
  and exactly one deployment branch policy, `{"name":"main","type":"branch"}`.
  IDs are assigned by GitHub and are not hardcoded. Existing reviewer rules are
  left untouched and remain enforced by GitHub; new reviewers are not required.
- Repository variable `TRACKING_TRUSTED_UPDATE_KEYS` is JSON
  `{"pilot-2026":"<base64 of the existing trusted 32-byte public key>"}`.
  Multiple independently trusted verification keys are allowed. No signing seed
  is needed, generated, downloaded or accepted from a candidate.
- Secret `TRACKING_BASELINE_URLS` is JSON
  `{"3.1.0":{target:{"url":signedURL}},"3.0.4":{target:{"url":signedURL}}}`.
  Expand each target map to all eight names in `tools/ci.py`: exactly 16 slots.
  Only these two private baselines require delegated URLs, even when acceptance
  requires seven versions. Keep the JSON within the unchanged 48 KiB limit.
  Only HTTPS `release-assets.githubusercontent.com` URLs with query strings are
  accepted, with no userinfo, fragment or nonstandard port. No redirects or
  GitHub Authorization headers are sent on these delegated requests.
- Public baselines `3.4.2`, `3.4.0`, `3.3.2`, `3.3.0`, and `3.2.0`, when required by
  `upgrade_baselines(version)`, come from the current `GITHUB_REPOSITORY`
  (`SalesOfTech/tracking-client`) through `GitHub.asset`. The harness reads the
  pinned asset ID and SHA-256 from `tools/upgrade-baselines.json`, checks API
  metadata for the exact `SOFT-Tracking-<target>-<version>.zip` name, ID, uploaded
  state, digest and bounded size, then verifies the downloaded bytes against the
  pinned SHA-256 and exact metadata size. GitHub credentials stay on the API
  request; any delegated asset redirect uses the same strict URL/no-auth rules.
  Missing or mismatched public assets fail without falling back to secret URLs.
- For compatibility, a complete URL map for every required version is also
  accepted within 48 KiB. All supplied URLs are validated and masked, but public
  slots are ignored for downloading and cannot override the pins. Partial mixed
  maps, extra versions/targets and digest overrides are rejected. Prefer the
  private-only map to avoid storing 56 expiring URLs.
- Issue the URLs only after the candidate is green. Preflight their usability
  privately, never print them, and remove the ephemeral secret after the run.
  Removing the secret is not URL revocation. Expired or unavailable URLs fail;
  rerun all jobs with fresh URLs rather than accepting partial results.

## What Is Bound

The harness checkout is the acceptance dispatch's exact main SHA. The candidate
is checked out separately under `candidate/` at the successful build's SHA.
The harness SHA may differ from the candidate SHA; this downloader-only change
does not require a candidate rebuild or a payload/source change.
Preflight validates the candidate workflow identity, main/manual event, completed
success, run attempt, all eight native jobs, candidate publication job, and
lightweight candidate tag SHA. It freezes the Release ID and all 16 public asset
IDs, sizes and API SHA-256 digests; downloaded checksum files must agree.

Every native runner verifies actual downloaded ZIP bytes, the existing pinned
old ZIP hashes, Ed25519 signatures with the independently supplied keyring,
manifest version/target/expiry/size, inner release SHA-256, candidate provenance,
inner build metadata and installer checksum. Missing digests or mismatches fail.
The source/build run provenance is unchanged; the separate harness SHA and
acceptance run/attempt/actors are recorded alongside it.

## Actual Baseline Tests

The downloader and original published-package path share
`smoke_published.stage_published_archive()`. The original private script is not
modified. Every required genuine version runs on each matching native runner using the
existing `smoke_update.py --published` assertions: original bootstrap, running
installer upgrade, new frozen health, extension switch, rollback and retained
queue/device/receipts. No synthetic substitute, rebuilt baseline, optional skip
or lowered archive limit can satisfy these cases. For `3.5.0`, the seven versions
are `3.4.2`, `3.4.0`, `3.3.2`, `3.3.0`, `3.2.0`, `3.1.0`, and `3.0.4`: all 56
version/target combinations remain mandatory. The download source does not alter
signature, staging, upgrade, rollback or retained-bootstrap assertions.

`TRACKING_SMOKE_CLIENT_ROOT` points the smoke process's client imports at the
candidate checkout, not the harness. A path check rejects mixed client modules.
The subprocess environment excludes URL secrets, GitHub tokens, runner tokens,
Python path overrides and optimization flags. All smoke stdout/stderr goes to
runner-temp files, never uploaded. Cleanup removes private downloads, logs and
isolated profiles even on ordinary failures. No artifact/cache upload step exists.
Only trusted reviewed code may run: output redirection is not a sandbox against
malicious code or intentional network exfiltration.

## Publication

Only eight successful native jobs with successful smoke steps permit promotion.
A clean runner with no baseline secret or private files revalidates the candidate
and creates a NEW `v<version>` tag at the candidate SHA and a draft stable release.
It copies unchanged current ZIP/checksum bytes, repeats signature/provenance
checks, then downloads and hashes all uploaded remote assets before setting
`draft=false, prerelease=false`. It remains non-Latest; its payload is the selected
stable version, not an RC. Release notes record both SHAs, actors, candidate/acceptance
run identities and the approved public asset hashes.

A fresh full acceptance run can resume an interrupted draft only at the exact
candidate SHA. Existing assets must be a subset of the accepted names, with
matching sizes and hashes verified by downloading them again. Verified matching
uploads are skipped; extra, incomplete or mismatched assets and any published
release are rejected. A matching orphan tag can also be reused. No tag is moved,
no asset is overwritten or deleted, and a partial set cannot be published.
No production catalog, backend deployment, or unrelated private server/platform
acceptance is performed or claimed.
