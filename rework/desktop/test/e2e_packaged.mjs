// 설치본(electron-builder 산출물, 퓨즈 적용·asar·PyInstaller 백엔드)을 실제로 띄워 시험한다.
//   xvfb-run -a node test/e2e_packaged.mjs [dist/linux-unpacked/rework]
// 퓨즈로 Node 인스펙터가 꺼져 있으므로 Playwright 의 electron.launch 대신 Chromium 원격 디버깅 포트(CDP)로 붙는다.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn, execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, '..');
const repo = path.resolve(desktop, '..');
const pw = await import(process.env.PLAYWRIGHT_PATH || 'playwright-core');
const { chromium } = pw.default || pw;
const bin = path.resolve(process.argv[2] || path.join(desktop, 'dist', 'linux-unpacked', 'rework'));

const results = [];
const check = (name, ok, detail = '') => results.push([name, !!ok, String(detail)]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms = 30000, step = 200) { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = await fn(); if (v) return v; await sleep(step); } return null; }
function get(port, p, headers = {}) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: p, headers, timeout: 4000 }, (res) => { let b = ''; res.on('data', (d) => (b += d)); res.on('end', () => resolve({ status: res.statusCode, body: b })); });
    req.on('error', () => resolve({ status: 0, body: '' })); req.on('timeout', () => { req.destroy(); resolve({ status: 0, body: '' }); });
  });
}
function pidsOf(pattern) {
  const out = [];
  for (const d of fs.readdirSync('/proc').filter((x) => /^\d+$/.test(x))) {
    try { if (fs.readFileSync(`/proc/${d}/cmdline`, 'utf8').replace(/\0/g, ' ').includes(pattern)) out.push(Number(d)); } catch { /* gone */ }
  }
  return out;
}
const backendPids = () => pidsOf('rework-backend --desktop');

check('0. 설치본 실행 파일이 있다', fs.existsSync(bin), bin);

// ---- 퓨즈 ----
const isAppImage = bin.endsWith('.AppImage');
const fuses = isAppImage ? { stdout: '' } : spawnSync('npx', ['--yes', '@electron/fuses', 'read', '--app', bin], { cwd: desktop, encoding: 'utf8' });
const f = fuses.stdout || '';
const flag = (name) => new RegExp(`${name} is (Enabled|Disabled)`).exec(f)?.[1];
if (!isAppImage) check('1. 퓨즈: RunAsNode·NODE_OPTIONS·Node 인스펙터 꺼짐, asar 무결성·asar 전용 켜짐',
  flag('RunAsNode') === 'Disabled' && flag('EnableNodeOptionsEnvironmentVariable') === 'Disabled' && flag('EnableNodeCliInspectArguments') === 'Disabled'
  && flag('EnableEmbeddedAsarIntegrityValidation') === 'Enabled' && flag('OnlyLoadAppFromAsar') === 'Enabled', f.replace(/\s+/g, ' ').slice(0, 260));
const asNode = isAppImage ? { stdout: '', stderr: '' } : spawnSync(bin, ['--no-sandbox', '-e', 'console.log("RAN-AS-NODE")'], { env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, encoding: 'utf8', timeout: 15000 });
check('1. ELECTRON_RUN_AS_NODE=1 로 임의 코드를 실행할 수 없다', !/RAN-AS-NODE/.test((asNode.stdout || '') + (asNode.stderr || '')), (asNode.stdout || asNode.stderr || '').slice(0, 80));

// ---- 실행 ----
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rework-pkg-e2e-'));
const env = { ...process.env, XDG_CONFIG_HOME: path.join(tmp, 'cfg'), HOME: tmp, APPIMAGE_EXTRACT_AND_RUN: '1' };
delete env.ELECTRON_RUN_AS_NODE;
const cdpPort = 9300 + Math.floor(Math.random() * 500);
const app = spawn(bin, ['--no-sandbox', `--remote-debugging-port=${cdpPort}`], { env, stdio: ['ignore', 'pipe', 'pipe'] });
let appLog = ''; app.stdout.on('data', (d) => (appLog += d)); app.stderr.on('data', (d) => (appLog += d));
let browser = null;
try {
  browser = await waitFor(async () => { try { return await chromium.connectOverCDP(`http://127.0.0.1:${cdpPort}`); } catch { return null; } }, 40000, 500);
  const ctx = browser.contexts()[0];
  const page = (await waitFor(async () => ctx.pages().find((p) => p.url().startsWith('http://127.0.0.1:')) || null, 90000)) || ctx.pages()[0];
  const errs = [];
  page.on('pageerror', (e) => errs.push('PAGEERROR ' + e.message));
  page.on('console', (m) => { if (/Content Security Policy|Refused to/i.test(m.text())) errs.push('CSP ' + m.text().slice(0, 140)); });
  await page.waitForSelector('#titlebar', { timeout: 30000 });
  const port = Number(new URL(page.url()).port);
  check('2. 설치본의 UI 가 번들된 백엔드에서 로드됨', /rework/.test(await page.title()) && !!(await page.$('#activitybar')), `${page.url()} ${await page.title()}`);
  const bp = backendPids();
  check('2. 백엔드는 PyInstaller 번들 실행 파일(하나)', bp.length === 1, bp.join(','));
  const about = await page.evaluate(() => fetch('/api/about').then((r) => r.json()));
  check('2. 동결(frozen) 백엔드·데스크톱 모드·사용자 데이터 폴더', about.frozen === true && about.desktop === true && about.data_dir.startsWith(path.join(tmp, 'cfg')), JSON.stringify(about).slice(0, 200));
  const benv = fs.readFileSync(`/proc/${bp[0]}/environ`, 'utf8');
  check('3. 백엔드 프로세스의 환경 변수·명령줄에 토큰이 없다(표준 입력으로만 전달)', !/REWORK_TOKEN/.test(benv) && !/token/i.test(fs.readFileSync(`/proc/${bp[0]}/cmdline`, 'utf8').replace('--token-stdin', '')), `pid ${bp[0]}`);
  const st0 = await get(port, '/api/about');
  check('3. 토큰 없이는 401', st0.status === 401, st0.status);
  const dmode = fs.statSync(about.data_dir).mode & 0o777;
  check('3. 데이터 폴더는 본인만 접근(0700)', dmode === 0o700, dmode.toString(8));

  // 열기 → 분석 → 격리 디버깅
  const demo = path.join(repo, 'samples', 'demo_elf');
  const opened = await page.evaluate((p) => fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: p }) }).then((r) => r.json()), demo);
  check('4. 설치본에서 바이너리 분석(lief·capstone 번들 동작)', opened.counts && opened.counts.functions >= 10, JSON.stringify(opened.counts));
  const sbx = await page.evaluate(() => fetch('/api/sandbox/status').then((r) => r.json()));
  check('4. 설치본에서도 격리 실행(bwrap·seccomp·gdb 보호) 사용 가능', sbx.status.available && sbx.guard.ready, sbx.status.reason || 'ok');
  const ran = await page.evaluate(async () => {
    const j = (m, u, b) => fetch(u, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then(async (r) => ({ s: r.status, j: await r.json() }));
    const s = await j('POST', '/api/debug/start', { breakpoints: ['main'] });
    const r = await j('POST', '/api/debug/exec', { action: 'run' });
    await j('POST', '/api/debug/stop');
    return { start: s.s, run: r.s, status: r.j.state && r.j.state.status, layers: r.j.state && r.j.state.sandbox && r.j.state.sandbox.summary };
  });
  check('4. 설치본에서 격리 디버깅이 main 에서 멈춘다', ran.start === 200 && ran.status === 'stopped' && /격리/.test(ran.layers || ''), JSON.stringify(ran));
  check('5. JS·CSP 오류 없음', errs.length === 0, errs.slice(0, 2).join(' / '));

  // ---- 종료: 앱을 닫으면 백엔드도 ----
  const before = backendPids();
  process.kill(app.pid, 'SIGTERM');
  const gone = await waitFor(async () => (backendPids().length === 0 && app.exitCode !== null ? true : null), 20000);
  check('6. 앱 종료(SIGTERM) 시 백엔드도 정리됨', !!gone && before.length === 1, `before=${before} after=${backendPids()} exit=${app.exitCode}`);
} catch (e) {
  check('실행 중 예외', false, (e.stack || e.message) + '\n앱 로그: ' + appLog.slice(-400));
}
try { app.kill('SIGKILL'); } catch { /* ignore */ }
await sleep(500);

// ---- 고아 방지: 셸이 SIGKILL 로 죽어도 백엔드가 남지 않는다 ----
{
  const app2 = spawn(bin, ['--no-sandbox'], { env: { ...env, XDG_CONFIG_HOME: path.join(tmp, 'cfg2') }, stdio: 'ignore' });
  const up = await waitFor(async () => (backendPids().length === 1 ? true : null), 60000);
  const bp2 = backendPids();
  try { process.kill(app2.pid, 'SIGKILL'); } catch { /* ignore */ }
  const orphanGone = await waitFor(async () => (backendPids().length === 0 ? true : null), 25000);
  check('7. 셸이 SIGKILL 로 죽어도 번들 백엔드가 고아로 남지 않음', !!up && !!orphanGone, `백엔드 ${bp2} → 남음 ${backendPids()}`);
  // 정리용: electron 자식 프로세스(zygote 등)가 남아 있으면 종료
  for (const pid of pidsOf(bin)) { try { process.kill(pid, 'SIGKILL'); } catch { /* ignore */ } }
}

let bad = 0;
for (const [n, ok, d] of results) { console.log((ok ? 'PASS ' : 'FAIL ') + n + '  ' + d.slice(0, 300)); bad += !ok; }
console.log(`\n== ${results.length - bad}/${results.length} 통과 ==`);
fs.rmSync(tmp, { recursive: true, force: true });
process.exit(bad ? 1 : 0);
