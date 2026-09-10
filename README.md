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

Run the candidate workflow manually from `main`. It creates a draft tagged
`candidate-<version>-<run_id>-<run_attempt>` at the exact source commit. Each passing
native job uploads only its new ZIP and SHA-256 file directly as Release assets,
without using Actions artifact storage. After all eight targets pass, the draft
becomes a prerelease, never marked Latest. Failed builds leave the release a draft;
inspect step logs and job summaries, then rerun all jobs for a new candidate tag.

Candidates do not publish production releases or alter the live update catalog.
Signing credentials are configured separately by authorized maintainers and are
never part of source.

Synthetic update and rollback tests do not establish compatibility with every
previous installation. Real published-version upgrade acceptance and production
promotion remain separate private gates. Retained baseline identifiers and hashes
are verification metadata, not bundled old installers or authorization tokens.

Keep local identities, queues, generated binaries and credentials out of commits.
Private browser extension installation may require an explicit browser confirmation.
Source availability does not disable OS permissions or device/company revocation.

Third-party license notices are retained alongside their components. No additional
license grant is implied by publication of this source.
