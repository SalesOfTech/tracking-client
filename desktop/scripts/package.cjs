'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {packager} = require('@electron/packager');
const {packagePolicy} = require('./package-policy.cjs');

(async () => {
  const policy = packagePolicy();
  const root = path.resolve(__dirname, '..'), stage = path.join(root, policy.stage);
  await fs.mkdir(stage, {recursive: true});
  for (const name of ['main.cjs', 'preload.cjs', 'protocol.cjs']) await fs.copyFile(path.join(root, name), path.join(stage, name));
  await fs.cp(path.join(root, 'dist'), path.join(stage, 'dist'), {recursive: true});
  await fs.copyFile(path.join(root, '../extension/icons/icon128.png'), path.join(stage, 'brand.png'));
  await fs.writeFile(path.join(stage, 'package.json'), JSON.stringify({name: 'soft-tracking-ui', version: policy.version, main: 'main.cjs', author: 'SalesOfTech', license: 'UNLICENSED', ...policy.metadata}));
  const packages = await packager({dir: stage, name: 'SoftTrackingUI', out: path.join(root, policy.output),
    platform: process.platform, arch: process.arch, electronVersion: '44.3.0',
    asar: true, overwrite: true, prune: false, appBundleId: 'com.soft.tracking.ui',
    appVersion: policy.version, executableName: 'SoftTrackingUI',
    ...(process.platform === 'darwin' ? {icon: path.join(root, '../agent/agent_tracker/assets/app_light.icns'),
      extendInfo: {CFBundleName: 'SOFT Tracking', CFBundleDisplayName: 'SOFT Tracking'}} : {}),
    ...(process.platform === 'win32' ? {icon: path.join(root, '../agent/agent_tracker/assets/app.ico'),
      win32metadata: {CompanyName: 'SalesOfTech', ProductName: 'SOFT Tracking', FileDescription: 'SOFT Tracking UI'}} : {}),
  });
  if (process.platform === 'darwin') {
    for (const output of packages) {
      const contents = path.join(output, 'SoftTrackingUI.app', 'Contents');
      // Packager overwrites extendInfo names with executableName; keep the
      // updater's executable path while assigning the user-visible Dock name.
      for (const key of ['CFBundleName', 'CFBundleDisplayName']) {
        execFileSync('/usr/bin/plutil', ['-replace', key, '-string', 'SOFT Tracking', path.join(contents, 'Info.plist')]);
      }
      execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', path.dirname(contents)]);
      const info = JSON.parse(execFileSync('/usr/bin/plutil', ['-convert', 'json', '-o', '-', path.join(contents, 'Info.plist')], {encoding: 'utf8'}));
      const expectedIcon = await fs.readFile(path.join(root, '../agent/agent_tracker/assets/app_light.icns'));
      const actualIcon = await fs.readFile(path.join(contents, 'Resources', info.CFBundleIconFile));
      if (info.CFBundleName !== 'SOFT Tracking' || info.CFBundleDisplayName !== 'SOFT Tracking' || !actualIcon.equals(expectedIcon)) {
        throw Error('macOS application branding verification failed');
      }
    }
  }
})().catch(error => {console.error(error.message); process.exitCode = 1;});
