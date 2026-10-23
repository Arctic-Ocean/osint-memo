'use strict';
/** 사용자 설정(창 위치·백엔드 실행 환경)을 userData/config.json 에 보관한다. 소유자만 읽고 쓰며, 원자적으로 저장한다. */
const fs = require('node:fs');
const path = require('node:path');

class Config {
  constructor(file) {
    this.file = file;
    this.data = {};
    try {
      const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) this.data = parsed;
    } catch { /* 없거나 깨졌으면 기본값 */ }
  }

  get(key, def) { return Object.prototype.hasOwnProperty.call(this.data, key) ? this.data[key] : def; }

  set(key, value) {
    this.data[key] = value;
    this.save();
  }

  save() {
    const tmp = `${this.file}.${process.pid}.tmp`;
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
      fs.writeFileSync(tmp, JSON.stringify(this.data, null, 1), { mode: 0o600 });
      fs.renameSync(tmp, this.file);
    } catch {
      try { fs.unlinkSync(tmp); } catch { /* ignore */ }
    }
  }
}

module.exports = { Config };
