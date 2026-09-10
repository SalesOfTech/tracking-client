'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {packagePolicy} = require('../scripts/package-policy.cjs');

test('packaging uses the installed dependency named packager export', () => {
  const dependency = require('@electron/packager');
  assert.equal(typeof dependency.packager, 'function');
  assert.equal(typeof dependency, 'object');
  const source = fs.readFileSync(path.join(__dirname, '../scripts/package.cjs'), 'utf8');
  assert.match(source, /const\s*\{\s*packager\s*\}\s*=\s*require\(['"]@electron\/packager['"]\)/);
  assert.match(source, /author:\s*'SalesOfTech'/);
  assert.match(source, /win32metadata:\s*\{CompanyName:\s*'SalesOfTech'/);
});

const localHost = {argv: ['--local-test'], env: {RELEASE_VERSION: '3.2.0-rc.1'},
  platform: 'win32', arch: 'x64', machine: 'AMD64'};

test('local packaging requires the explicit flag and never changes GitHub environment', () => {
  const env = {RELEASE_VERSION: '3.2.0-rc.1'};
  assert.throws(() => packagePolicy({...localHost, argv: [], env}), /only in GitHub Actions/);
  const policy = packagePolicy({...localHost, env});
  assert.deepEqual(env, {RELEASE_VERSION: '3.2.0-rc.1'});
  assert.deepEqual(policy, {localTest: true, version: '3.2.0-rc.1', stage: '.stage-local-test',
    output: 'out/local-test', metadata: {build_channel: 'local-test'}});
});

test('normal GitHub packaging keeps its original guard, output and metadata', () => {
  for (const platform of ['win32', 'darwin', 'linux']) {
    const policy = packagePolicy({...localHost, argv: [], platform, env: {GITHUB_ACTIONS: 'true'}});
    assert.equal(policy.localTest, false);
    assert.equal(policy.output, 'out');
    assert.equal(policy.stage, '.stage');
    assert.deepEqual(policy.metadata, {});
  }
});

test('local exception rejects non-Windows, non-x64 and ARM emulation even on GitHub', () => {
  for (const host of [{platform: 'darwin'}, {platform: 'linux'}, {arch: 'ia32'},
    {arch: 'arm64'}, {machine: 'ARM64'}, {machine: 'unknown'}]) {
    assert.throws(() => packagePolicy({...localHost, ...host, env: {...localHost.env, GITHUB_ACTIONS: 'true'}}),
      /native Windows x64/);
  }
  for (const machine of ['AMD64', 'x64', 'x86_64']) assert.equal(packagePolicy({...localHost, machine}).localTest, true);
});

test('local package version must be explicit prerelease and remain below stable', () => {
  for (const version of ['', '3.2.0', '3.2.0-rc', '3.2.0-rc.10000', '65536.2.0-rc.1',
    '3.02.0-rc.1', '../3.2.0-rc.1', '3.2.0-rc.1+local']) {
    assert.throws(() => packagePolicy({...localHost, env: {RELEASE_VERSION: version}}), /prerelease/);
  }
  for (const version of ['3.2.0-alpha.0', '3.2.0-beta.2', '3.2.0-rc.1']) {
    assert.equal(packagePolicy({...localHost, env: {RELEASE_VERSION: version}}).version, version);
  }
});

test('unknown, duplicate and environment-only local flags do not bypass policy', () => {
  for (const argv of [['--local'], ['--local-test', '--local-test'], ['--local-test=true'], ['--arch=x64']]) {
    assert.throws(() => packagePolicy({...localHost, argv}), /Unknown packaging argument/);
  }
  assert.throws(() => packagePolicy({...localHost, argv: [], env: {LOCAL_TEST: 'true'}}), /GitHub Actions/);
});
