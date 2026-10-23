'use strict';
/**
 * 렌더러에 노출하는 최소 API. 컨텍스트 격리·샌드박스 상태에서 contextBridge 로만 연결한다.
 *  - 분석 UI(http://127.0.0.1:포트): 끌어다 놓은 파일의 실제 경로, 네이티브 ‘열기’ 대화상자의 결과 수신
 *  - 셸이 만든 오류 화면(file://): 다시 시작·종료·로그 폴더 열기
 * 어느 쪽이든 Node/Electron 객체를 그대로 넘기지 않고, 받는 값은 문자열만 허용한다.
 */
const { contextBridge, ipcRenderer, webUtils } = require('electron');

if (location.protocol === 'file:') {
  contextBridge.exposeInMainWorld('reworkShell', {
    info: () => ipcRenderer.invoke('rework:error-info'),
    restart: () => ipcRenderer.send('rework:restart'),
    quit: () => ipcRenderer.send('rework:quit'),
    openLogs: () => ipcRenderer.send('rework:open-logs'),
  });
} else {
  contextBridge.exposeInMainWorld('reworkDesktop', {
    isDesktop: true,
    platform: process.platform,
    /** 끌어다 놓은 File 의 실제 경로(복사 없이 그 자리에서 연다). 얻을 수 없으면 빈 문자열. */
    pathForFile: (file) => { try { return webUtils.getPathForFile(file) || ''; } catch { return ''; } },
    /** 이 경로를 백엔드가 보는 경로로 바꾼다(WSL2 모드에서 C:\... → /mnt/c/...). */
    backendPath: (p) => ipcRenderer.invoke('rework:backend-path', String(p)),
    /** 메뉴의 ‘열기…’ 로 고른 경로(이미 백엔드 경로)를 받는다. */
    onOpenPath: (cb) => {
      ipcRenderer.on('rework:open-path', (_e, p) => { if (typeof p === 'string' && p) cb(p); });
    },
    /** 메뉴의 ‘폴더 열기…’ 로 고른 작업 폴더(이미 백엔드 경로)를 받는다. */
    onOpenFolder: (cb) => {
      ipcRenderer.on('rework:open-folder', (_e, p) => { if (typeof p === 'string' && p) cb(p); });
    },
  });
}
