'use strict';
const os = require('node:os');

function packagePolicy({argv = process.argv.slice(2), env = process.env,
  platform = process.platform, arch = process.arch, machine = os.machine()} = {}) {
  if (argv.length > 1 || (argv.length === 1 && argv[0] !== '--local-test')) throw Error('Unknown packaging argument');
  const localTest = argv[0] === '--local-test';
  const version = env.RELEASE_VERSION || '3.2.0';
  if (!localTest) {
    if (env.GITHUB_ACTIONS !== 'true') throw Error('Native packages are built only in GitHub Actions');
  } else {
    if (platform !== 'win32' || arch !== 'x64' || !['amd64', 'x64', 'x86_64'].includes(machine.toLowerCase())) {
      throw Error('Local test packaging is restricted to native Windows x64');
    }
    if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-(alpha|beta|rc)\.(0|[1-9]\d*)$/.test(version)
        || version.split('-')[0].split('.').some(part => Number(part) > 65535)
        || Number(version.split('.').at(-1)) > 9999) {
      throw Error('Local test packaging requires an explicit bounded prerelease RELEASE_VERSION');
    }
  }
  return {localTest, version, stage: localTest ? '.stage-local-test' : '.stage',
    output: localTest ? 'out/local-test' : 'out', metadata: localTest ? {build_channel: 'local-test'} : {}};
}

module.exports = {packagePolicy};
