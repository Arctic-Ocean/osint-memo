'use strict';
/**
 * 분석 백엔드(Python) 프로세스 수명주기.
 *
 *  - 포트는 백엔드가 `--port 0` 으로 직접 고른다(미리 바인딩한 소켓을 쓰므로 포트를 알아냈다 닫는 경쟁이 없다). 표준 출력의
 *    `REWORK_PORT=<번호>` 한 줄로 받는다.
 *  - 토큰은 셸이 만든 무작위 값이며 표준 입력 첫 줄로만 넘긴다(명령줄·환경 변수·로그에 남기지 않는다 — 같은 사용자의 다른 프로세스가
 *    /proc/<pid>/environ 으로 환경 변수를 읽을 수 있다).
 *  - 표준 입력 파이프를 열어 둔다: 셸이 어떤 이유로든 죽으면 파이프가 닫혀 백엔드가 스스로 종료한다(고아 방지).
 */
const { spawn, spawnSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { EventEmitter } = require('node:events');

const LOG_MAX_LINES = 400;
const LOG_FILE_MAX = 2 * 1024 * 1024;

function newToken() {
  return crypto.randomBytes(24).toString('base64url');           // ASCII 만(백엔드는 비 ASCII 토큰을 거부한다)
}

/** 개발 중에는 저장소의 venv·run.py, 설치본에서는 묶여 들어온 실행 파일을 쓴다. */
function resolveCommand({ packaged, resourcesPath, repoRoot, mode, platform = process.platform, env = process.env, exists = fs.existsSync }) {
  const exe = platform === 'win32' ? 'rework-backend.exe' : 'rework-backend';
  const ARGS = ['--desktop', '--token-stdin', '--port', '0'];
  if (!packaged && env.REWORK_BACKEND_BIN) return { cmd: env.REWORK_BACKEND_BIN, args: ARGS, cwd: undefined, kind: 'env' };   // 개발용 지정은 설치본에서 무시한다
  if (mode === 'wsl') {
    // Windows 에서 WSL2 안의 리눅스 백엔드를 실행한다(디버거·격리 실행은 리눅스에서만 동작한다)
    const linuxBin = packaged ? path.join(resourcesPath, 'backend-wsl', 'rework-backend')
                              : path.join(repoRoot, 'desktop', 'build', 'backend-wsl', 'rework-backend');
    return { cmd: 'wsl.exe', args: ['--exec', toWslPath(linuxBin), ...ARGS], cwd: undefined, kind: 'wsl', wslBin: linuxBin };
  }
  if (packaged) {
    const bin = path.join(resourcesPath, 'backend', exe);
    return { cmd: bin, args: ARGS, cwd: path.dirname(bin), kind: 'bundled' };
  }
  const venvPy = platform === 'win32' ? path.join(repoRoot, '.venv', 'Scripts', 'python.exe') : path.join(repoRoot, '.venv', 'bin', 'python');
  const py = (!packaged && env.REWORK_PYTHON) || (exists(venvPy) ? venvPy : (platform === 'win32' ? 'python' : 'python3'));
  return { cmd: py, args: [path.join(repoRoot, 'run.py'), ...ARGS], cwd: repoRoot, kind: 'dev' };
}

/** Windows 경로 → WSL 경로. `C:\a\b` → `/mnt/c/a/b`, `\\wsl.localhost\Ubuntu\home\x` → `/home/x`. 변환할 수 없으면 그대로. */
function toWslPath(p) {
  let m = /^\\\\(?:wsl\.localhost|wsl\$)\\[^\\]+(\\.*)?$/i.exec(p);
  if (m) return (m[1] || '\\').replace(/\\/g, '/');
  m = /^([A-Za-z]):[\\/](.*)$/.exec(p);
  if (m) return `/mnt/${m[1].toLowerCase()}/${m[2].replace(/\\/g, '/')}`;
  return p;
}

class Backend extends EventEmitter {
  constructor(opts) {
    super();
    this.opts = opts;                    // { packaged, resourcesPath, repoRoot, dataDir, logDir, mode }
    this.token = newToken();
    this.port = null;
    this.proc = null;
    this.lines = [];                     // 최근 로그(오류 화면에 보여 준다)
    this.stopping = false;
    this.logFd = null;
    this.exitInfo = null;
    this.ready = false;                  // 응답을 확인한 뒤에만 true — 그 전의 종료는 start() 의 실패로 다루고 crashed 를 내지 않는다
  }

  get origin() { return `http://127.0.0.1:${this.port}`; }

  _log(text) {
    for (const ln of String(text).split(/\r?\n/)) {
      if (!ln) continue;
      this.lines.push(ln.slice(0, 1000));
    }
    if (this.lines.length > LOG_MAX_LINES) this.lines.splice(0, this.lines.length - LOG_MAX_LINES);
    if (this.logFd !== null) {
      try {
        if (fs.fstatSync(this.logFd).size < LOG_FILE_MAX) fs.writeSync(this.logFd, text.endsWith('\n') ? text : text + '\n');
      } catch { /* 로그 실패가 앱을 막지 않는다 */ }
    }
  }

  tail(n = 40) { return this.lines.slice(-n).join('\n'); }

  _openLog() {
    try {
      fs.mkdirSync(this.opts.logDir, { recursive: true, mode: 0o700 });
      this.logFd = fs.openSync(path.join(this.opts.logDir, 'backend.log'), 'w', 0o600);   // 실행마다 새로(토큰은 출력하지 않는다)
    } catch { this.logFd = null; }
  }

  /** 백엔드를 띄우고 응답할 때까지 기다린다. 실패하면 사유와 함께 reject. */
  async start(timeoutMs = 90000) {
    if (this.proc) throw new Error('이미 실행 중입니다.');
    this.stopping = false; this.exitInfo = null; this.port = null; this.lines = []; this.ready = false;
    this._openLog();
    const spec = resolveCommand({ ...this.opts, repoRoot: this.opts.repoRoot, mode: this.opts.mode });
    this.spec = spec;
    const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' };
    delete env.REWORK_TOKEN;                                      // 토큰은 환경에 두지 않는다(아래에서 표준 입력으로 넘긴다)
    if (spec.kind !== 'wsl') env.REWORK_DATA = this.opts.dataDir; // WSL 에서는 리눅스 홈(~/.rework/data)을 쓴다
    this._log(`[셸] 백엔드 시작: ${spec.kind} ${path.basename(spec.cmd)}`);
    let proc;
    try {
      proc = spawn(spec.cmd, spec.args, { cwd: spec.cwd, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    } catch (e) { throw new Error(`백엔드를 실행하지 못했습니다: ${e.message}`); }
    this.proc = proc;
    const portPromise = new Promise((resolve, reject) => {
      let buf = '';
      const onOut = (d) => {
        const s = d.toString('utf8'); this._log(s);
        if (this.port === null) {
          buf += s;
          const m = /REWORK_PORT=(\d{2,5})/.exec(buf);
          if (m) { this.port = Number(m[1]); resolve(this.port); }
        }
      };
      proc.stdout.on('data', onOut);
      proc.stderr.on('data', (d) => { const s = d.toString('utf8'); this._log(s); if (this.port === null) buf += s; });
      proc.on('error', (e) => reject(new Error(`백엔드를 실행하지 못했습니다: ${e.message}`)));
      proc.on('exit', (code, signal) => {
        this.exitInfo = { code, signal };
        this._log(`[셸] 백엔드 종료 code=${code} signal=${signal}`);
        this.proc = null;
        if (this.port === null) reject(new Error(`백엔드가 시작 도중 종료되었습니다(코드 ${code ?? signal}).`));
        if (!this.stopping && this.ready) this.emit('crashed', this.exitInfo);
        this.emit('exit', this.exitInfo);
      });
    });
    proc.stdin.on('error', () => { /* 종료 중 EPIPE 무시 */ });
    try { proc.stdin.write(this.token + '\n'); } catch { /* 종료된 프로세스: exit 이벤트가 처리 */ }
    const deadline = Date.now() + timeoutMs;
    let timer;
    try {
      await Promise.race([portPromise, new Promise((_, rej) => { timer = setTimeout(() => rej(new Error('백엔드가 포트를 알려 주지 않았습니다(시간 초과).')), timeoutMs); })]);
      await this._waitReady(deadline);
    } catch (e) {
      await this.stop();
      throw e;
    } finally { clearTimeout(timer); }
    this.ready = true;
    return this.port;
  }

  _waitReady(deadline) {
    return new Promise((resolve, reject) => {
      const attempt = () => {
        if (!this.proc) return reject(new Error('백엔드가 준비되기 전에 종료되었습니다.'));
        const req = http.get({ host: '127.0.0.1', port: this.port, path: '/api/about', headers: { 'X-Rework-Token': this.token }, timeout: 2000 }, (res) => {
          res.resume();
          if (res.statusCode === 200) return resolve();
          retry();
        });
        req.on('error', retry);
        req.on('timeout', () => { req.destroy(); retry(); });
      };
      const retry = () => (Date.now() > deadline ? reject(new Error('백엔드가 응답하지 않습니다(시간 초과).')) : setTimeout(attempt, 150));
      attempt();
    });
  }

  /** 정상 종료(표준 입력 닫기 → 백엔드가 세션·샌드박스를 정리) 후 안 되면 강제 종료. */
  async stop(graceMs = 8000) {
    const proc = this.proc;
    this.stopping = true;
    if (!proc) { this._closeLog(); return; }
    const exited = new Promise((resolve) => proc.once('exit', resolve));
    try { proc.stdin.end(); } catch { /* ignore */ }
    const t = setTimeout(() => this._kill(proc), graceMs);
    await exited;
    clearTimeout(t);
    this._closeLog();
  }

  _kill(proc) {
    try {
      if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(proc.pid), '/T', '/F'], { windowsHide: true });
      else proc.kill('SIGKILL');
    } catch { /* ignore */ }
  }

  _closeLog() {
    if (this.logFd !== null) { try { fs.closeSync(this.logFd); } catch { /* ignore */ } this.logFd = null; }
  }
}

module.exports = { Backend, resolveCommand, toWslPath, newToken };
