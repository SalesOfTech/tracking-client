# SOFT Tracking Client

Client source and build candidates for the SOFT Tracking desktop agent and private
browser extension. The desktop uses Electron and HeroUI; compatible legacy targets
retain the Qt interface. Russian, English, Czech and Uzbek interfaces are included.

This repository contains no customer enrollment, server implementation, production
configuration, signing private keys or operational deployment history. A company
installation and employee activation code are still required to use the service.
The service enforces collection policy and domain/application selection. Preview
data is fictional; preview mode must not be used with a real employee profile.

## Source

- `agent/`: Python collection runtime, installer, local state and update verification.
- `extension/`: browser collection, offline queue and multilingual installation guide.
- `desktop/`: isolated Electron UI and typed local bridge.
- `tools/`: native packaging, build-contract tests and offline smoke checks.

## Development Checks

Use Python 3.10 and Node.js 22 for the current desktop toolchain. Native targets
use their pinned dependencies and target-specific matrix in GitHub Actions.

```sh
python -m pip install -r agent/requirements-build.txt
python tools/install_qt.py
npm ci
npm ci --prefix desktop
npm test
npm test --prefix desktop
npm run build --prefix desktop
python -m unittest discover -s tools/tests -v
PYTHONPATH=agent python -m unittest discover -s agent/tests -v
```

Desktop tests require a graphical session; Linux CI uses Xvfb. Browser checks run
with Playwright in CI. Build commands do not require employee credentials.

## Build Candidates

Use payload version `3.2.0`, without an RC suffix. A stable version in the binaries
does not imply release approval. The [3.2.0 release plan](tools/release-plan-3.2.0.md)
separates eight-target builds, exact-artifact private acceptance, normal public
release publication, and subsequent production catalog promotion.

Run the candidate workflow manually from `main`. It creates a draft tagged
`candidate-<version>-<run_id>-<run_attempt>` at the exact source commit. Each passing
native job uploads only its new ZIP and SHA-256 file directly as Release assets,
without using Actions artifact storage. After all eight targets pass, the draft
becomes a prerelease, never marked Latest. Failed builds leave the release a draft;
inspect step logs and job summaries, then rerun all jobs for a new candidate tag.

Candidates do not publish production releases or alter the live update catalog.
Signing credentials are configured separately by authorized maintainers and are
never part of source.

### macOS Packaging and Trust

The macOS Python runtime uses PyInstaller onefile packaging so its versioned
`Python.framework` symlinks survive the regular-file-only update ZIP. The executable
paths and launcher protocol stay unchanged for existing installations. Windows
and Linux retain their onedir runtime layout. Native macOS builds validate the
embedded framework aliases or the standalone `libpython.dylib` runtime used by
newer Intel Python distributions, and verify executable and installer bundle signatures
before creating the DMG; installation and update smoke tests remain required.

These integrity checks and ad-hoc signatures do **not** establish Apple trust.
Without Developer ID signing and Apple notarization, a downloaded build can still
be blocked by Gatekeeper, even after the framework packaging defect is fixed.
Do not advertise such a build as requiring no security exceptions. Acceptance of
that requirement needs a downloaded installer tested on a clean, Gatekeeper-enabled
Mac, not just a successful launch on a CI runner. The installer must not disable
Gatekeeper or remove quarantine attributes to hide this limitation.

Synthetic update and rollback tests do not establish compatibility with every
previous installation. Real published-version upgrade acceptance and production
promotion remain separate private gates. Retained baseline identifiers and hashes
are verification metadata, not bundled old installers or authorization tokens.

Keep local identities, queues, generated binaries and credentials out of commits.
Private browser extension installation may require an explicit browser confirmation.
Source availability does not disable OS permissions or device/company revocation.

## Optional Extended Activity

Version 3.5.0 understands the server policy `extended_activity: true`. This is
off when absent and still requires tracking to be enabled. It adds active-time
sessions for websites and foreground applications outside the company's work
list. Browser sessions contain only the origin for these sites; page contents,
titles, paths and entered values are not collected. Existing interaction rules
remain restricted to explicitly configured work domains. Private browser tabs
are excluded. Automatic application collection excludes common system UI,
bundled utilities, the tracker itself and supported browsers (to avoid counting
their time twice); explicitly configured work applications keep their rules.

The desktop shows the extended scope on its connection and settings screens.
The backend must explicitly enable and accept this policy before additional
data is sent. A site outside the work list is not proof of unproductive work;
the data records observed usage, not an employment assessment.

Third-party license notices are retained alongside their components. No additional
license grant is implied by publication of this source.
