const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
const shortRoot = 'C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\_MEI123\\setup-payload\\electron\\resources\\app.asar';
const longRoot = shortRoot.replace('RUNNER~1', 'runneradmin');
const expectedIndex = path.win32.join(longRoot, 'dist', 'index.html');
const expectedPage = pathToFileURL(expectedIndex, {windows: true}).href;

function fixture(canonicalize = () => expectedIndex) {
  const calls = [];
  const webContents = {mainFrame: {url: expectedPage + '?mode=installer'}};
  const context = vm.createContext({__dirname: shortRoot, path: path.win32,
    fs: {realpathSync: {native: filename => {calls.push(filename); return canonicalize(filename);}}},
    pathToFileURL: filename => pathToFileURL(filename, {windows: true}),
    window: {webContents},
  });
  const indexAndPage = source.match(/^const index = .+;\r?\nconst page = .+;/m)[0];
  const authorize = source.match(/^function authorized\(event\) \{[\s\S]*?^\}/m)[0];
  vm.runInContext(indexAndPage + '\n' + authorize, context);
  return {calls, webContents, index: vm.runInContext('index', context),
    authorized: event => context.authorized(event),
    event: {sender: webContents, senderFrame: webContents.mainFrame},
  };
}

test('installer canonicalizes its 8.3 ASAR path before loading and authorizing the exact page', () => {
  const f = fixture();
  assert.deepEqual(f.calls, [path.win32.join(shortRoot, 'dist', 'index.html')]);
  assert.equal(f.index, expectedIndex);
  assert.notEqual(pathToFileURL(f.calls[0], {windows: true}).href, expectedPage);
  assert.match(source, /window\.loadFile\(index, \{query: \{mode\}\}\)/);
  assert.equal(f.authorized(f.event), true);
});

test('canonicalization does not authorize other URLs or sibling files', () => {
  const f = fixture();
  for (const url of ['https://untrusted.example/index.html', 'about:blank',
    expectedPage.replace('/index.html', '/other.html'), expectedPage + '.untrusted',
    expectedPage.replace('runneradmin', 'another-user'),
    pathToFileURL(path.win32.join(shortRoot, 'dist', 'index.html'), {windows: true}).href]) {
    f.webContents.mainFrame.url = url;
    assert.equal(f.authorized(f.event), false, url);
  }
});

test('only the owning webContents and exact main frame may issue IPC', () => {
  const f = fixture();
  assert.equal(f.authorized({...f.event, sender: {}}), false);
  assert.equal(f.authorized({...f.event, senderFrame: {url: expectedPage}}), false);
  assert.equal(f.authorized({...f.event, senderFrame: null}), false);
});

test('missing canonical page fails closed instead of using an unchecked URL', () => {
  assert.throws(() => fixture(() => {throw Error('ENOENT');}), /ENOENT/);
});
