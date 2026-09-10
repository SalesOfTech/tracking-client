# Desktop Agent

The Python runtime owns device enrollment, server policy, collection, durable event
delivery and signed-update verification. Electron is an isolated UI; selected
legacy targets use Qt. The browser extension communicates through native messaging.

See the root [README](../README.md) for development checks and candidate build scope.
The multilingual installation guide is bundled in `extension/setup.html`.

Do not remove a local queue to fix a connection problem: pending events remain
until the server confirms storage. Never commit device identities, employee keys,
company installation codes or signing private keys. Test and preview identities
are synthetic, separate from real company enrollment.
