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
const check = (name, ok, detail = '') => { results.push([name, !!ok, String(detail)]); console.log((ok ? 'PASS ' : 'FAIL ') + name + '  ' + String(detail).slice(0, 300)); };   // 멈춰도 어디까지 갔는지 남도록 바로 출력
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
// AppImage 런타임은 Electron 을 자식으로 띄우고 기다리는 래퍼라, 래퍼에 보낸 SIGTERM·SIGKILL 은 래퍼만 죽이고 앱(Electron)은 그대로 둔다.
// 앱의 종료 처리를 시험하려면 신호를 실제 Electron 메인 프로세스(--type= 없는 `.../appimage_extracted_*/rework`)에 보내야 한다.
function electronMainPids(bin) {
  const out = [];
  for (const d of fs.readdirSync('/proc').filter((x) => /^\d+$/.test(x))) {
    try {
      const a = fs.readFileSync(`/proc/${d}/cmdline`, 'utf8').split('\0');
      const isMain = bin.endsWith('.AppImage') ? /\/appimage_extracted_[^/]+\/rework$/.test(a[0]) : a[0] === bin;
      if (isMain && !a.some((x) => x.startsWith('--type='))) out.push(Number(d));
    } catch { /* gone */ }
  }
  return out;
}
const mainTarget = (launcher) => (bin.endsWith('.AppImage') ? electronMainPids(bin)[0] || launcher.pid : launcher.pid);

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
  // 소스 편집: 작업 폴더 열기(경로 입력) → Monaco(동결 백엔드가 서빙, 엄격한 CSP·작업자) → 저장
  const work = path.join(tmp, 'work');
  fs.mkdirSync(work, { recursive: true });
  fs.writeFileSync(path.join(work, 'hello.c'), 'int main(void) {\n    return 0;\n}\n');
  await page.click('.act[data-view="explorer"]');
  await page.fill('.ws-path-input', work);
  await page.press('.ws-path-input', 'Enter');
  await page.locator('.ws-row', { hasText: 'hello.c' }).first().click({ timeout: 20000 });
  const mon = await waitFor(async () => ((await page.$('.monaco-editor')) ? true : null), 40000);
  check('4. 설치본에서 작업 폴더를 열고 Monaco 편집기가 뜬다(동결 백엔드·엄격한 CSP·작업자)', !!mon, '');
  if (mon) {
    const ko = await page.evaluate(() => window.monaco.editor.getEditors()[0].getAction('actions.find').label);
    await page.click('.monaco-editor .view-lines');
    await page.keyboard.press('Control+End');
    await page.keyboard.type('// 설치본에서 저장');
    await page.keyboard.press('Control+s');
    const saved = await waitFor(async () => (fs.readFileSync(path.join(work, 'hello.c'), 'utf8').includes('설치본에서 저장') ? true : null), 10000);
    check('4. 설치본에서 편집기가 한국어이고 Ctrl+S 가 디스크에 저장된다', ko === '찾기' && !!saved, ko);
  }

  // ---- 이후에 추가된 기능을 설치본(동결 백엔드)에서: 디버거 심화·빌드 러너·노트북 ----
  const api = (m, u, b) => page.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { s: r.status, j: await r.json().catch(() => null) };
  }, [m, u, b]);
  await api('POST', '/api/debug/start', { breakpoints: ['main'] });
  const dr = await api('POST', '/api/debug/exec', { action: 'run' });
  const maps = await api('GET', '/api/debug/maps');
  const dump = await api('GET', '/api/debug/stackdump?count=16');
  const lp = await api('POST', '/api/debug/break', { op: 'log', location: 'main', template: 'hit %d', args: ['1'] });
  await api('POST', '/api/debug/stop');
  check('4. 설치본에서 디버거 심화(메모리 맵·스택 덤프·로그포인트)가 동작한다',
    dr.j.state.status === 'stopped' && maps.s === 200 && maps.j.regions.length > 3 && dump.s === 200 && lp.s === 200, `${maps.s} regions=${maps.j && maps.j.regions && maps.j.regions.length} dump=${dump.s} log=${lp.s}`);

  const job = await (async () => {
    const st = await api('POST', '/api/run/start', { command: 'gcc -o hello hello.c && ./hello; echo exit=$? uid=$(id -u)', timeout: 60 });
    let s = st.j;
    for (let i = 0; i < 300 && s && s.status === 'running'; i++) { await sleep(200); s = (await api('GET', '/api/run/state?since=0')).j; }
    return { start: st.s, ...(s || {}) };
  })();
  const uid = /uid=(\d+)/.exec(job.output || '');
  check('4. 설치본에서 러너가 격리 안에서 gcc 로 빌드·실행하고 결과물을 작업 폴더로 되가져온다',
    job.start === 200 && job.status === 'done' && job.sandboxed === true && /exit=0/.test(job.output) && uid && uid[1] !== '0'
    && fs.existsSync(path.join(work, 'hello')) && (job.changes || []).some((c) => c.path === 'hello' && c.status === 'new'),
    `${job.status} sandboxed=${job.sandboxed} ${(job.output || '').trim().slice(-60)} changes=${(job.changes || []).map((c) => c.path + ':' + c.status)}`);

  fs.writeFileSync(path.join(work, 'nb.ipynb'), JSON.stringify({
    nbformat: 4, nbformat_minor: 5, metadata: {},
    cells: [{ cell_type: 'code', id: 'a1', metadata: {}, execution_count: null, outputs: [],
      source: 'x = 6 * 7\nprint(x)\nfs = [f for f in rk.functions() if rk.name(f.vaddr) == "main"]\nrk.decompile(fs[0].vaddr)[:40]' }] }));
  await page.evaluate(() => document.querySelector('.ws-bar button[title*="새로"]')?.click());
  await page.locator('.ws-row[data-rel="nb.ipynb"]').click({ timeout: 20000 });
  await page.waitForSelector('.nb-cell textarea.nb-src', { timeout: 20000 });
  await page.click('.nb-cell textarea.nb-src');
  await page.keyboard.press('Shift+Enter');
  const nbOut = await waitFor(async () => {
    const t = await page.locator('.nb-cell .nb-out').first().innerText().catch(() => '');
    return /Out\[1\]/.test(t) ? t : null;
  }, 30000);
  check('4. 설치본에서 노트북 셀이 실행된다(상태 유지 커널·print·마지막 식·rk.decompile)', !!nbOut && /(^|\n)42\n/.test(nbOut) && /main|int/.test(nbOut), (nbOut || '').replace(/\n/g, ' ⏎ ').slice(0, 120));
  await page.keyboard.press('Control+s');
  const nbSaved = await waitFor(async () => {
    try { const j = JSON.parse(fs.readFileSync(path.join(work, 'nb.ipynb'), 'utf8')); return j.cells[0].execution_count === 1 && j.cells[0].outputs.length >= 2 ? j : null; } catch { return null; }
  }, 10000);
  check('4. 설치본에서 노트북 Ctrl+S 가 출력과 함께 nbformat 4 로 저장된다', !!nbSaved && nbSaved.nbformat === 4, nbSaved ? nbSaved.cells[0].outputs.map((o) => o.output_type).join(',') : '저장 안 됨');

  // ---- 백엔드가 죽으면 오류 화면 → 다시 시작 (패키지된 앱에서 file:// 이 막혀 빈 Chromium 오류창이 뜨던 결함의 회귀 방지) ----
  const killed = backendPids();
  process.kill(killed[0], 'SIGKILL');
  const errShown = await waitFor(async () => (page.url().startsWith('rework-shell://') && (await page.$('#msg')) ? true : null), 20000);
  const msgTxt = errShown ? await page.$eval('#msg', (e) => e.textContent) : `url=${page.url()}`;
  check('4. 설치본: 백엔드가 죽으면 오류 화면(사유 표시)이 뜬다', errShown && /예기치 않게 종료/.test(msgTxt), msgTxt.slice(0, 100));
  if (errShown) {
    await page.click('#restart');
    const back = await waitFor(async () => (page.url().startsWith('http://127.0.0.1:') ? page.url() : null), 60000);
    await page.waitForSelector('#titlebar', { timeout: 30000 });
    const again = backendPids();
    check('4. 설치본: 다시 시작하면 새 백엔드로 복구', !!back && again.length === 1 && again[0] !== killed[0] && (await page.evaluate(() => fetch('/api/about').then((r) => r.status))) === 200, `${killed[0]} → ${again}`);
  }
  check('5. JS·CSP 오류 없음', errs.length === 0, errs.slice(0, 2).join(' / '));

  // ---- 종료: 앱을 닫으면 백엔드도 ----
  const before = backendPids();
  process.kill(mainTarget(app), 'SIGTERM');
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
  try { process.kill(mainTarget(app2), 'SIGKILL'); } catch { /* ignore */ }
  const orphanGone = await waitFor(async () => (backendPids().length === 0 ? true : null), 25000);
  check('7. 셸이 SIGKILL 로 죽어도 번들 백엔드가 고아로 남지 않음', !!up && !!orphanGone, `백엔드 ${bp2} → 남음 ${backendPids()}`);
  // 정리용: electron 자식 프로세스(zygote 등)가 남아 있으면 종료
  // (명령줄 어디에든 경로가 들어 있는 프로세스를 죽이면, 절대 경로를 인자로 준 이 시험 자신과 셸 래퍼까지 죽는다 — argv[0] 이 그 실행 파일인 것만)
  for (const d of fs.readdirSync('/proc').filter((x) => /^\d+$/.test(x))) {
    try { if (Number(d) === process.pid) continue;
      const argv0 = fs.readFileSync(`/proc/${d}/cmdline`, 'utf8').split('\0')[0];
      if (argv0 === bin || (isAppImage && /\/appimage_extracted_[^/]+\//.test(argv0))) process.kill(Number(d), 'SIGKILL'); } catch { /* gone */ }
  }
}

let bad = 0;
for (const [, ok] of results) bad += !ok;
console.log(`\n== ${results.length - bad}/${results.length} 통과 ==`);
fs.rmSync(tmp, { recursive: true, force: true });
process.exit(bad ? 1 : 0);
