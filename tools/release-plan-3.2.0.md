# 3.2.0 Acceptance and Stable Publication

The separate `.github/workflows/accept-release.yml` now implements this path.
The candidate build workflow remains candidate-only. No build, acceptance,
publication, environment configuration or secret operation is performed locally.

## Operator Contract

- Finish all eight candidate build jobs successfully with payload version `3.2.0`.
- Commit the acceptance harness separately; rebuilding the candidate is unnecessary.
- Dispatch `accept-release.yml` from `main` with `candidate_tag` exactly
  `candidate-3.2.0-<run_id>-<run_attempt>`.
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
  Only HTTPS `release-assets.githubusercontent.com` URLs with query strings are
  accepted, with no userinfo, fragment or nonstandard port. No redirects or
  GitHub Authorization headers are sent on these delegated requests.
- Issue the URLs only after the candidate is green. Preflight their usability
  privately, never print them, and remove the ephemeral secret after the run.
  Removing the secret is not URL revocation. Expired or unavailable URLs fail;
  rerun all jobs with fresh URLs rather than accepting partial results.

## What Is Bound

The harness checkout is the acceptance dispatch's exact main SHA. The candidate
is checked out separately under `candidate/` at the successful build's SHA.
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
modified. Both genuine versions run on each matching native runner using the
existing `smoke_update.py --published` assertions: original bootstrap, running
installer upgrade, new frozen health, extension switch, rollback and retained
queue/device/receipts. No synthetic substitute, rebuilt baseline, optional skip
or lowered archive limit can satisfy these 16 cases.

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
and creates a NEW `v3.2.0` tag at the candidate SHA and a draft stable release.
It copies unchanged current ZIP/checksum bytes, repeats signature/provenance
checks, then downloads and hashes all uploaded remote assets before setting
`draft=false, prerelease=false`. It remains non-Latest; its payload is normal
`3.2.0`, not an RC. Release notes record both SHAs, actors, candidate/acceptance
run identities and the approved public asset hashes.

A fresh full acceptance run can resume an interrupted draft only at the exact
candidate SHA. Existing assets must be a subset of the accepted names, with
matching sizes and hashes verified by downloading them again. Verified matching
uploads are skipped; extra, incomplete or mismatched assets and any published
release are rejected. A matching orphan tag can also be reused. No tag is moved,
no asset is overwritten or deleted, and a partial set cannot be published.
No production catalog, backend deployment, or unrelated private server/platform
acceptance is performed or claimed.
