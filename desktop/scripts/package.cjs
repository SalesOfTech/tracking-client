'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
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
  await packager({dir: stage, name: 'SoftTrackingUI', out: path.join(root, policy.output),
    platform: process.platform, arch: process.arch, electronVersion: '44.3.0',
    asar: true, overwrite: true, prune: false, appBundleId: 'com.soft.tracking.ui',
    appVersion: policy.version, executableName: 'SoftTrackingUI',
    ...(process.platform === 'win32' ? {icon: path.join(root, '../agent/agent_tracker/assets/app.ico'),
      win32metadata: {CompanyName: 'SalesOfTech', ProductName: 'SOFT Tracking', FileDescription: 'SOFT Tracking UI'}} : {}),
  });
})().catch(error => {console.error(error.message); process.exitCode = 1;});
