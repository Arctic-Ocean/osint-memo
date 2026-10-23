'use strict';
// 순수 로직 단위 테스트(Electron 없이): node --test test/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { resolveCommand, toWslPath, newToken } = require('../src/backend');
const { Config } = require('../src/config');

test('토큰은 ASCII·충분히 길고·매번 다르다', () => {
  const a = newToken(), b = newToken();
  assert.match(a, /^[A-Za-z0-9_-]{32,}$/);
  assert.notEqual(a, b);
});

test('toWslPath: 드라이브·UNC·이미 리눅스 경로', () => {
  assert.equal(toWslPath('C:\\Users\\me\\a.exe'), '/mnt/c/Users/me/a.exe');
  assert.equal(toWslPath('d:/tools/x'), '/mnt/d/tools/x');
  assert.equal(toWslPath('\\\\wsl.localhost\\Ubuntu\\home\\me\\a'), '/home/me/a');
  assert.equal(toWslPath('\\\\wsl$\\Kali\\tmp\\z'), '/tmp/z');
  assert.equal(toWslPath('\\\\wsl.localhost\\Ubuntu'), '/');
  assert.equal(toWslPath('/home/me/a'), '/home/me/a');
  assert.equal(toWslPath('relative\\path'), 'relative\\path');
});

test('resolveCommand: 설치본·개발·WSL·환경 지정', () => {
  const base = { packaged: true, resourcesPath: '/opt/rework/resources', repoRoot: '/repo', mode: 'native', platform: 'linux', env: {}, exists: () => false };
  let c = resolveCommand(base);
  assert.equal(c.cmd, '/opt/rework/resources/backend/rework-backend');
  assert.deepEqual(c.args, ['--desktop', '--token-stdin', '--port', '0']);
  c = resolveCommand({ ...base, platform: 'win32', resourcesPath: 'C:\\app\\resources' });
  assert.ok(c.cmd.endsWith('rework-backend.exe'));
  c = resolveCommand({ ...base, packaged: false });
  assert.equal(c.kind, 'dev'); assert.equal(c.cmd, 'python3'); assert.equal(c.args[0], path.join('/repo', 'run.py'));
  c = resolveCommand({ ...base, packaged: false, exists: (p) => p === path.join('/repo', '.venv', 'bin', 'python') });
  assert.equal(c.cmd, path.join('/repo', '.venv', 'bin', 'python'));
  c = resolveCommand({ ...base, packaged: false, env: { REWORK_PYTHON: '/usr/bin/py' } });
  assert.equal(c.cmd, '/usr/bin/py');
  c = resolveCommand({ ...base, platform: 'win32', mode: 'wsl', resourcesPath: 'C:\\app\\resources' });
  assert.equal(c.cmd, 'wsl.exe'); assert.equal(c.args[0], '--exec');
  assert.ok(c.args[1].startsWith('/mnt/c/'), c.args[1]);
  c = resolveCommand({ ...base, packaged: false, env: { REWORK_BACKEND_BIN: '/x/bin' } });
  assert.equal(c.cmd, '/x/bin');
  // 설치본은 환경 변수로 실행 파일·파이썬을 바꿀 수 없다(환경을 쥔 쪽이 앱 권한으로 임의 프로그램을 돌리는 통로가 되지 않게)
  c = resolveCommand({ ...base, env: { REWORK_BACKEND_BIN: '/evil/bin', REWORK_PYTHON: '/evil/py' } });
  assert.equal(c.cmd, '/opt/rework/resources/backend/rework-backend');
});

test('Config: 저장·재로드·깨진 파일은 기본값·권한 0600', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rw-cfg-'));
  const f = path.join(dir, 'sub', 'config.json');
  const c = new Config(f);
  assert.equal(c.get('x', 7), 7);
  c.set('backendMode', 'wsl'); c.set('bounds', { x: 1, y: 2, width: 800, height: 600 });
  const c2 = new Config(f);
  assert.equal(c2.get('backendMode'), 'wsl');
  assert.deepEqual(c2.get('bounds'), { x: 1, y: 2, width: 800, height: 600 });
  if (process.platform !== 'win32') assert.equal(fs.statSync(f).mode & 0o777, 0o600);
  fs.writeFileSync(f, '{깨진');
  assert.equal(new Config(f).get('backendMode', 'native'), 'native');
  fs.writeFileSync(f, '[1,2]');
  assert.equal(new Config(f).get('backendMode', 'native'), 'native');
  assert.deepEqual(fs.readdirSync(path.dirname(f)).filter((n) => n.endsWith('.tmp')), []);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('package.json 버전은 백엔드 __version__ 과 같다', () => {
  const pkg = require('../package.json');
  const init = fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'app', '__init__.py'), 'utf8');
  assert.match(init, new RegExp(`__version__ = "${pkg.version.replace(/\./g, '\\.')}"`));
});
