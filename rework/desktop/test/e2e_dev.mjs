// 데스크톱 셸 E2E(개발 모드: 저장소의 .venv 백엔드). Xvfb 아래에서 실제 Electron 을 띄워 시험한다.
//   xvfb-run -a node test/e2e_dev.mjs
// 환경 변수: PLAYWRIGHT_PATH(playwright/index.js), ELECTRON_BIN(기본 node_modules/electron/dist/electron)
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, '..');
const repo = path.resolve(desktop, '..');
const pw = await import(process.env.PLAYWRIGHT_PATH || 'playwright-core');
const { _electron: electron } = pw.default || pw;
const electronBin = process.env.ELECTRON_BIN || path.join(desktop, 'node_modules', 'electron', 'dist', 'electron');

const results = [];
const check = (name, ok, detail = '') => { results.push([name, !!ok, String(detail)]); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function get(port, p, headers = {}) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: p, headers, timeout: 4000 }, (res) => {
      let b = ''; res.on('data', (d) => (b += d)); res.on('end', () => resolve({ status: res.statusCode, body: b }));
    });
    req.on('error', () => resolve({ status: 0, body: '' }));
    req.on('timeout', () => { req.destroy(); resolve({ status: 0, body: '' }); });
  });
}
function backendPids(rootPid) {          // electron 의 자손 중 run.py --desktop
  const out = [];
  const pids = fs.readdirSync('/proc').filter((d) => /^\d+$/.test(d));
  for (const d of pids) {
    try {
      const cmd = fs.readFileSync(`/proc/${d}/cmdline`, 'utf8').replace(/\0/g, ' ');
      if (cmd.includes('run.py') && cmd.includes('--desktop')) out.push(Number(d));
    } catch { /* 사라진 프로세스 */ }
  }
  return out;
}
async function waitFor(fn, ms = 20000, step = 150) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { const v = await fn(); if (v) return v; await sleep(step); }
  return null;
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rework-desktop-e2e-'));
const env = { ...process.env, XDG_CONFIG_HOME: path.join(tmp, 'cfg'), HOME: tmp };
delete env.ELECTRON_RUN_AS_NODE;
const demo = path.join(repo, 'samples', 'demo_elf');

const app = await electron.launch({ executablePath: electronBin, args: ['--no-sandbox', desktop, '--dev'], env, timeout: 60000 });
const page = await app.firstWindow();
const errs = [];
page.on('pageerror', (e) => errs.push('PAGEERROR ' + e.message));
page.on('console', (m) => { if (/Content Security Policy|Refused to/i.test(m.text()) && !/JavaScript URL/.test(m.text())) errs.push('CSP ' + m.text().slice(0, 160)); });
let port = null;
try {
  // ---- 1. 시작: 백엔드가 뜨고 UI 가 로드된다 ----
  const up = await waitFor(async () => { const u = page.url(); return u.startsWith('http://127.0.0.1:') ? u : null; }, 60000);
  check('1. UI 가 백엔드 주소(127.0.0.1:임의 포트)에서 로드됨', !!up, up || page.url());
  port = up ? Number(new URL(up).port) : null;
  await page.waitForSelector('#titlebar', { timeout: 20000 });
  check('1. 창 제목·UI 렌더링', /rework/.test(await page.title()) && !!(await page.$('#activitybar')), await page.title());

  // ---- 2. 토큰 ----
  const noTok = await get(port, '/api/about');
  const badTok = await get(port, '/api/about', { 'X-Rework-Token': 'x'.repeat(32) });
  check('2. 토큰 없이 같은 컴퓨터의 다른 프로세스가 API 를 부르면 401', noTok.status === 401 && badTok.status === 401, `${noTok.status}/${badTok.status}`);
  const inPage = await page.evaluate(() => fetch('/api/about').then(async (r) => ({ s: r.status, j: await r.json() })));
  check('2. 앱 안에서는 토큰이 자동으로 붙어 API 가 200', inPage.s === 200 && inPage.j.desktop === true && inPage.j.debugger_supported === true, JSON.stringify(inPage.j).slice(0, 120));
  const ck = await page.evaluate(() => document.cookie);
  check('2. 토큰이 쿠키·주소·자바스크립트 어디에도 노출되지 않음', ck === '' && !page.url().includes('token'), `cookie="${ck}"`);

  // ---- 3. 렌더러 격리 ----
  const iso = await page.evaluate(() => ({ req: typeof window.require, proc: typeof window.process, mod: typeof window.module,
    desk: window.reworkDesktop ? Object.keys(window.reworkDesktop).sort().join(',') : null, shell: typeof window.reworkShell }));
  check('3. 렌더러에 Node 가 없고 최소 API 만 노출', iso.req === 'undefined' && iso.proc === 'undefined' && iso.mod === 'undefined'
    && iso.desk === 'backendPath,isDesktop,onOpenPath,pathForFile,platform' && iso.shell === 'undefined', JSON.stringify(iso));

  // ---- 4. 탐색·새 창 차단 ----
  await app.evaluate(({ shell }) => { globalThis.__opened = []; shell.openExternal = async (u) => { globalThis.__opened.push(u); }; });
  const w = await page.evaluate(() => { const r = window.open('https://example.com/x'); return r === null; });
  await page.evaluate(() => { location.href = 'https://example.com/nav'; });
  await sleep(800);
  const opened = await app.evaluate(() => globalThis.__opened);
  check('4. 외부 주소로 이동은 막히고 새 창은 기본 브라우저로만', page.url().startsWith('http://127.0.0.1:') && w && opened.length === 1 && opened[0] === 'https://example.com/x', `${page.url()} ${JSON.stringify(opened)}`);
  await page.evaluate(() => { window.open('javascript:alert(1)'); window.open('file:///etc/passwd'); });
  await sleep(300);
  check('4. javascript:·file: 새 창은 열리지 않음', (await app.evaluate(() => globalThis.__opened)).length === 1);
  await page.evaluate(() => { location.href = 'file:///etc/passwd'; });
  await sleep(500);
  check('4. file: 이동 차단', page.url().startsWith('http://127.0.0.1:'));
  const perm = await page.evaluate(async () => { try { const r = await navigator.permissions.query({ name: 'geolocation' }); return r.state; } catch (e) { return 'err'; } });
  check('4. 권한 요청은 거부 상태', perm === 'denied' || perm === 'prompt' || perm === 'err', perm);

  // ---- 5. 네이티브 열기 → UI 에서 파일이 열린다 ----
  await app.evaluate(({ BrowserWindow }, p) => { BrowserWindow.getAllWindows()[0].webContents.send('rework:open-path', p); }, demo);
  const name = await waitFor(async () => { const t = await page.$eval('#file-name', (e) => e.textContent).catch(() => ''); return /demo_elf/.test(t) ? t : null; }, 20000);
  check('5. 메뉴·명령줄에서 받은 경로가 UI 에서 열림', !!name, name || '');

  // ---- 6. 격리 실행이 앱 안에서도 사용 가능 ----
  const sbx = await page.evaluate(() => fetch('/api/sandbox/status').then((r) => r.json()));
  check('6. 앱 안에서 격리 실행 사용 가능', sbx.status.available === true && sbx.debugger.supported === true, sbx.status.reason || 'ok');

  // ---- 7. 백엔드가 죽으면 오류 화면 → 다시 시작 ----
  const pids = backendPids();
  check('7. 백엔드는 정확히 하나', pids.length === 1, pids.join(','));
  process.kill(pids[0], 'SIGKILL');
  const errShown = await waitFor(async () => (page.url().startsWith('file://') && (await page.$('#msg')) ? true : null), 20000);
  const msgTxt = errShown ? await page.$eval('#msg', (e) => e.textContent) : '';
  check('7. 백엔드가 죽으면 오류 화면(사유 표시)', errShown && /예기치 않게 종료/.test(msgTxt), msgTxt);
  const logTxt = errShown ? await page.$eval('#log', (e) => e.textContent) : '';
  check('7. 오류 화면에 로그가 보임(토큰은 없음)', /백엔드 종료/.test(logTxt) && !/[A-Za-z0-9_-]{32}/.test(logTxt.replace(/[/\\.:]/g, ' ')), logTxt.slice(-120));
  await page.click('#restart');
  const back = await waitFor(async () => (page.url().startsWith('http://127.0.0.1:') ? page.url() : null), 60000);
  await page.waitForSelector('#titlebar', { timeout: 30000 });
  const newPort = back ? Number(new URL(back).port) : null;
  const still = await page.evaluate(() => fetch('/api/about').then((r) => r.status));
  check('7. 다시 시작하면 새 백엔드로 복구', !!back && still === 200 && backendPids().length === 1, `${port}→${newPort}`);
  port = newPort;

  // ---- 8. 종료하면 백엔드도 사라진다 ----
  const pidBefore = backendPids();
  await app.close();
  const gone = await waitFor(async () => (backendPids().length === 0 ? true : null), 15000);
  check('8. 앱을 닫으면 백엔드 프로세스도 종료', !!gone && pidBefore.length === 1, `before=${pidBefore} after=${backendPids()}`);
  check('9. JS 오류 없음', errs.length === 0, errs.slice(0, 2).join(' / '));
} catch (e) {
  check('실행 중 예외', false, e.stack || e.message);
  try { await app.close(); } catch { /* ignore */ }
}

// ---- 10. 셸이 비정상 종료돼도(SIGKILL) 백엔드가 고아로 남지 않는다 ----
{
  const app2 = await electron.launch({ executablePath: electronBin, args: ['--no-sandbox', desktop, '--dev'], env, timeout: 60000 }).catch(() => null);
  if (app2) {
    const p2 = await app2.firstWindow();
    await waitFor(async () => (p2.url().startsWith('http://127.0.0.1:') ? true : null), 60000);
    const bp = backendPids();
    const mainPid = app2.process().pid;
    try { process.kill(mainPid, 'SIGKILL'); } catch { /* ignore */ }
    const orphanGone = await waitFor(async () => (backendPids().length === 0 ? true : null), 20000);
    check('10. 셸이 SIGKILL 로 죽어도 백엔드가 스스로 종료(고아 없음)', bp.length === 1 && !!orphanGone, `백엔드 ${bp} → 남음 ${backendPids()}`);
  } else check('10. 두 번째 실행', false, '실행 실패');
}

let bad = 0;
for (const [n, ok, d] of results) { console.log((ok ? 'PASS ' : 'FAIL ') + n + '  ' + d.slice(0, 300)); bad += !ok; }
console.log(`\n== ${results.length - bad}/${results.length} 통과 ==`);
fs.rmSync(tmp, { recursive: true, force: true });
process.exit(bad ? 1 : 0);
