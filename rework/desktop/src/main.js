'use strict';
/**
 * rework 데스크톱 셸(Electron 메인 프로세스).
 *
 * 하는 일: 분석 백엔드(Python)를 띄우고 → 그 서버의 UI 를 창에 보여 주고 → 끝날 때 정리한다.
 * 보안 모델: 창에 들어가는 것은 이 컴퓨터의 백엔드(127.0.0.1:임의 포트) UI 뿐이다. 외부 주소 이동·새 창·권한 요청은 모두 막고,
 * 백엔드 API 는 셸이 만든 무작위 토큰(요청 헤더로만 전달, 쿠키·주소에 남지 않음)이 있어야 응답한다. 따라서 같은 컴퓨터의 다른 사용자·
 * 프로세스는 포트를 알아도 API(스크립트 실행·격리 끄기 포함)를 부를 수 없다.
 */
const { app, BrowserWindow, Menu, dialog, ipcMain, screen, session, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');

const { Backend, toWslPath } = require('./backend');
const { Config } = require('./config');
const { buildMenu } = require('./menu');

app.setName('rework');
// 이 창은 127.0.0.1 의 백엔드와만 통신한다: 시스템 프록시(PAC 포함)를 거치지 않게 한다(PAC 가 로컬 주소를 외부로 보내거나 주소를 새게 하는 일 방지).
app.commandLine.appendSwitch('no-proxy-server');

const REPO_ROOT = path.resolve(__dirname, '..', '..');                // 개발 중: rework/
const PAGES = path.join(__dirname, 'pages');
const ERROR_URL_PREFIX = require('node:url').pathToFileURL(PAGES).href;
const IS_DEV = !app.isPackaged;

let win = null;
let backend = null;
let config = null;
let quitting = false;
let pendingOpen = null;                    // UI 가 뜬 뒤에 열 파일(명령줄·두 번째 실행에서 받은 것)
let lastError = null;                      // 오류 화면에 보여 줄 { message, log, hint }

// ---- 단일 인스턴스 ---------------------------------------------------------------
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', (_e, argv) => {
    if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
    const f = fileArg(argv.slice(1));
    if (f) openFileInUi(f);
  });
}

function fileArg(args) {
  for (const a of args) {
    if (!a || a.startsWith('-')) continue;
    try { if (fs.statSync(a).isFile()) return path.resolve(a); } catch { /* 파일이 아님 */ }
  }
  return null;
}

// ---- 경로·설정 ---------------------------------------------------------------------
function backendMode() {
  if (process.platform !== 'win32') return 'native';
  return config.get('backendMode', 'native') === 'wsl' ? 'wsl' : 'native';
}

function toBackendPath(p) {
  return backendMode() === 'wsl' ? toWslPath(p) : p;
}

function backendOpts() {
  const userData = app.getPath('userData');
  return {
    packaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot: REPO_ROOT,
    dataDir: path.join(userData, 'data'),
    logDir: path.join(userData, 'logs'),
    mode: backendMode(),
  };
}

// ---- 창 -----------------------------------------------------------------------------
function restoreBounds() {
  const def = { width: 1360, height: 860 };
  const b = config.get('bounds', null);
  if (!b || typeof b !== 'object') return def;
  const ok = [b.x, b.y, b.width, b.height].every((n) => Number.isFinite(n));
  if (!ok || b.width < 600 || b.height < 400) return def;
  const disp = screen.getDisplayMatching({ x: b.x, y: b.y, width: b.width, height: b.height });
  const wa = disp.workArea;
  const inside = b.x < wa.x + wa.width - 80 && b.x + b.width > wa.x + 80 && b.y < wa.y + wa.height - 80 && b.y + b.height > wa.y;
  return inside ? { x: b.x, y: b.y, width: b.width, height: b.height } : def;
}

function createWindow() {
  win = new BrowserWindow({
    ...restoreBounds(),
    minWidth: 900, minHeight: 600,
    show: false,
    backgroundColor: '#0b1522',
    title: 'rework',
    icon: path.join(__dirname, '..', 'assets', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
      devTools: IS_DEV,
    },
  });
  if (config.get('maximized', false)) win.maximize();
  win.once('ready-to-show', () => win && win.show());
  win.on('close', () => {
    if (!win) return;
    config.set('maximized', win.isMaximized());
    if (!win.isMaximized() && !win.isMinimized() && !win.isFullScreen()) config.set('bounds', win.getBounds());
  });
  win.on('closed', () => { win = null; });
  win.webContents.on('did-finish-load', () => {
    if (pendingOpen && backend && backend.ready && win && win.webContents.getURL().startsWith(backend.origin)) {
      const p = pendingOpen; pendingOpen = null;
      sendOpenPath(p);
    }
  });
  Menu.setApplicationMenu(buildMenu({
    isDev: IS_DEV,
    platform: process.platform,
    getWindow: () => win,
    getMode: backendMode,
    openFile: chooseAndOpenFile,
    setMode: switchMode,
    about: showAbout,
    openLogs: () => shell.openPath(path.join(app.getPath('userData'), 'logs')),
    reload: () => { if (win && backend && backend.ready) win.webContents.reloadIgnoringCache(); },
    quit: () => app.quit(),
  }));
}

// ---- 보안: 탐색·새 창·권한 ---------------------------------------------------------------
app.on('web-contents-created', (_e, contents) => {
  const guard = (e, url) => {
    // 창 안에서는 이 앱의 백엔드 origin 으로만 이동할 수 있다(드롭한 파일로 이동·외부 사이트 이동 차단)
    if (!(backend && backend.port && url.startsWith(backend.origin + '/'))) e.preventDefault();
  };
  contents.on('will-navigate', guard);
  contents.on('will-redirect', guard);
  contents.on('will-attach-webview', (e) => e.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    try {
      const u = new URL(url);
      if (u.protocol === 'https:' || u.protocol === 'http:') {
        if (!(backend && backend.port && u.origin === backend.origin)) shell.openExternal(u.toString());   // 외부 링크는 기본 브라우저로
      }
    } catch { /* 올바르지 않은 주소는 무시 */ }
    return { action: 'deny' };
  });
});

function hardenSession() {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  ses.setPermissionCheckHandler(() => false);
  ses.setDevicePermissionHandler(() => false);
}

/** 백엔드 origin 으로 가는 요청에만 토큰 헤더를 붙인다(다른 어떤 주소로도 토큰이 나가지 않는다). */
function hookToken(origin, token) {
  const ses = session.defaultSession;
  ses.webRequest.onBeforeSendHeaders({ urls: [`${origin}/*`] }, (details, cb) => {
    details.requestHeaders['X-Rework-Token'] = token;
    cb({ requestHeaders: details.requestHeaders });
  });
}

// ---- 화면 전환 ------------------------------------------------------------------------
function showSplash() { if (win) win.loadFile(path.join(PAGES, 'splash.html')); }

function showError(message, hint) {
  lastError = { message, log: backend ? backend.tail(60) : '', hint: hint || '' };
  if (win) win.loadFile(path.join(PAGES, 'error.html'));
}

async function boot() {
  showSplash();
  if (backend) { await backend.stop().catch(() => {}); }
  backend = new Backend(backendOpts());
  const mine = backend;
  mine.on('crashed', (info) => {
    if (quitting || backend !== mine) return;
    showError(`분석 엔진이 예기치 않게 종료되었습니다(코드 ${info.code ?? info.signal}). 진행 중이던 디버그 세션은 끝났습니다.`,
              '“다시 시작”을 누르면 엔진을 다시 띄웁니다. 계속되면 로그를 확인하세요.');
  });
  try {
    await mine.start();
  } catch (e) {
    if (quitting || backend !== mine) return;
    showError(String(e.message || e), startHint(mine));
    return;
  }
  if (quitting || backend !== mine) return;
  hookToken(mine.origin, mine.token);
  if (win) await win.loadURL(`${mine.origin}/`).catch((e) => { if (!quitting && backend === mine) showError(`화면을 불러오지 못했습니다: ${e.message}`, ''); });
  if (backendMode() === 'wsl') win && win.setTitle('rework (WSL2)');
}

function startHint(b) {
  if (b.spec && b.spec.kind === 'wsl') return 'WSL2 모드입니다. WSL2 가 설치돼 있고 배포판이 하나 이상 있는지 확인하세요(`wsl --status`). 메뉴 > 실행 환경 에서 Windows 기본으로 돌아갈 수 있습니다.';
  if (b.spec && b.spec.kind === 'dev') return '개발 모드: 저장소의 .venv 가 있는지, `pip install -r requirements.txt` 를 했는지 확인하세요.';
  return '설치가 손상되었을 수 있습니다. 다시 설치하거나 로그를 확인하세요.';
}

async function switchMode(mode) {
  if (process.platform !== 'win32' || (mode !== 'wsl' && mode !== 'native')) return;
  config.set('backendMode', mode);
  await boot();
}

// ---- 파일 열기 --------------------------------------------------------------------------
function sendOpenPath(p) {
  if (win && backend && backend.ready) win.webContents.send('rework:open-path', toBackendPath(p));
}

function openFileInUi(p) {
  if (win && backend && backend.ready && win.webContents.getURL().startsWith(backend.origin)) sendOpenPath(p);
  else pendingOpen = p;
}

async function chooseAndOpenFile() {
  if (!win || !backend || !backend.ready) return;
  const r = await dialog.showOpenDialog(win, { title: '분석할 파일 열기', properties: ['openFile'] });
  if (!r.canceled && r.filePaths[0]) sendOpenPath(r.filePaths[0]);
}

function showAbout() {
  const lines = [`rework ${app.getVersion()}`, `Electron ${process.versions.electron} · Chromium ${process.versions.chrome}`,
                 `실행 환경: ${process.platform}${backendMode() === 'wsl' ? ' (WSL2 백엔드)' : ''}`,
                 `데이터 폴더: ${path.join(app.getPath('userData'), 'data')}`];
  dialog.showMessageBox(win || undefined, { type: 'info', title: 'rework 정보', message: 'rework — 리버스 엔지니어링 워크벤치', detail: lines.join('\n') });
}

// ---- IPC(보낸 프레임을 검증한다) -----------------------------------------------------------------
function fromErrorPage(e) {
  const u = e.senderFrame && e.senderFrame.url;
  return typeof u === 'string' && u.startsWith(ERROR_URL_PREFIX);
}
function fromUi(e) {
  const u = e.senderFrame && e.senderFrame.url;
  return typeof u === 'string' && backend && backend.port && u.startsWith(backend.origin + '/');
}

ipcMain.handle('rework:error-info', (e) => (fromErrorPage(e) ? (lastError || { message: '알 수 없는 오류', log: '', hint: '' }) : null));
ipcMain.on('rework:restart', (e) => { if (fromErrorPage(e)) boot(); });
ipcMain.on('rework:quit', (e) => { if (fromErrorPage(e)) app.quit(); });
ipcMain.on('rework:open-logs', (e) => { if (fromErrorPage(e)) shell.openPath(path.join(app.getPath('userData'), 'logs')); });
ipcMain.handle('rework:backend-path', (e, p) => (fromUi(e) && typeof p === 'string' ? toBackendPath(p) : ''));

// ---- 수명주기 ---------------------------------------------------------------------------
app.on('before-quit', (e) => {
  if (quitting) return;
  quitting = true;
  e.preventDefault();
  const done = () => app.exit(0);
  if (!backend) return done();
  backend.stop().then(done, done);
});
app.on('window-all-closed', () => app.quit());
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => app.quit());

app.whenReady().then(async () => {
  if (!app.hasSingleInstanceLock()) return;
  config = new Config(path.join(app.getPath('userData'), 'config.json'));
  hardenSession();
  pendingOpen = fileArg(process.argv.slice(app.isPackaged ? 1 : 2));
  createWindow();
  await boot();
});
