// Monaco 웹 워커 진입점(같은 출처 파일). 편집기 코어·언어 서비스가 이 워커 안에서 돌아 화면이 멈추지 않는다.
self.MonacoEnvironment = { baseUrl: self.location.origin + '/vendor/monaco/' };
importScripts(self.location.origin + '/vendor/monaco/vs/base/worker/workerMain.js');
