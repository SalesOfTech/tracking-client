# Legacy Chromium profile

The main manifest is MV3. Older operating systems can be capped at a browser
version that cannot load MV3. In particular, Chrome 88 dropped macOS 10.10;
Chrome 87 needs a separate MV2 background-page manifest. Windows 8.1 is capped
at Chrome 109, which supports MV3.

For the legacy package, derive its manifest from the main manifest:

- `manifest_version: 2`
- move `host_permissions` into `permissions`, then remove `host_permissions`
- rename `action` to `browser_action`
- replace background with `{scripts: ["privacy.js", "outbox.js", "background.js"], persistent: false}`
- retain the public key/extension ID and the same collector/privacy/outbox code

The shared background code uses callback-based storage and the correct action
API on both profiles. `tools/release.py` derives the MV2 manifest for the macOS
legacy target and removes the MV3 scripting permission. This is not certification:
no MV2 package has been tested on a legacy browser yet. Do not offer
MV2 to current Chrome, disable browser security or install old browsers for users.

References:
- https://storage.googleapis.com/support-kms-prod/5vpwgHqbezFemAospC1r7MUB5OapQAxu8M7k
- https://support.google.com/chrome/a/answer/7100626?hl=en
