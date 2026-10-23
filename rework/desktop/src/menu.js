'use strict';
/** 한국어 애플리케이션 메뉴. 동작은 main.js 가 콜백으로 넘긴다. */
const { Menu } = require('electron');

function buildMenu({ isDev, platform, getWindow, getMode, openFile, openFolder, setMode, about, openLogs, reload, quit }) {
  const file = [
    { label: '바이너리 열기…', accelerator: 'CmdOrCtrl+O', click: () => openFile() },
    { label: '폴더 열기(소스 편집)…', accelerator: 'CmdOrCtrl+Shift+O', click: () => openFolder() },
    { type: 'separator' },
    { label: '종료', accelerator: platform === 'win32' ? 'Alt+F4' : 'CmdOrCtrl+Q', click: () => quit() },
  ];
  const view = [
    { label: '새로 고침', accelerator: 'CmdOrCtrl+R', click: () => reload() },
    { type: 'separator' },
    { label: '확대', accelerator: 'CmdOrCtrl+Plus', role: 'zoomIn' },
    { label: '축소', accelerator: 'CmdOrCtrl+-', role: 'zoomOut' },
    { label: '원래 크기', accelerator: 'CmdOrCtrl+0', role: 'resetZoom' },
    { type: 'separator' },
    { label: '전체 화면', accelerator: 'F11', role: 'togglefullscreen' },
  ];
  if (isDev) view.push({ type: 'separator' }, { label: '개발자 도구', accelerator: 'CmdOrCtrl+Shift+I', role: 'toggleDevTools' });
  const template = [
    { label: '파일', submenu: file },
    { label: '편집', submenu: [
      { label: '실행 취소', role: 'undo' }, { label: '다시 실행', role: 'redo' }, { type: 'separator' },
      { label: '잘라내기', role: 'cut' }, { label: '복사', role: 'copy' }, { label: '붙여넣기', role: 'paste' },
      { label: '모두 선택', role: 'selectAll' },
    ] },
    { label: '보기', submenu: view },
  ];
  if (platform === 'win32') {
    template.push({ label: '실행 환경', submenu: [
      { label: 'Windows 기본 (정적 분석만)', type: 'radio', checked: getMode() === 'native', click: () => setMode('native') },
      { label: 'WSL2 (디버거·격리 실행 사용)', type: 'radio', checked: getMode() === 'wsl', click: () => setMode('wsl') },
    ] });
  }
  template.push({ label: '도움말', submenu: [
    { label: '로그 폴더 열기', click: () => openLogs() },
    { type: 'separator' },
    { label: 'rework 정보', click: () => about() },
  ] });
  return Menu.buildFromTemplate(template);
}

module.exports = { buildMenu };
