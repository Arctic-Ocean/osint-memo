"""AI 채팅 프록시.

로컬 전용 백엔드가 분석가 대신 LLM 서비스로 질문을 전달한다.
세 가지 제공자를 지원한다:
  - ollama           : 로컬 Ollama (기본, 외부 전송 없음)
  - openai           : OpenAI 호환 /v1/chat/completions (LM Studio·llama.cpp 등 포함)
  - anthropic        : Claude API (/v1/messages)

설정(API 키 포함)은 데이터 디렉터리의 ai.json에 저장한다. 저장소 암호화는
이후 과제이므로, 현재는 키를 평문으로 보관한다는 점을 UI에서 안내한다.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

_DEFAULTS = {
    "provider": "ollama",
    "base": "http://127.0.0.1:11434",
    "model": "llama3.1",
    "api_key": "",
    "temperature": 0.2,
    "max_tokens": 1024,
}


class AIConfig:
    def __init__(self, path: str):
        self.path = path
        self.data = dict(_DEFAULTS)
        self.load()

    def load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self.data.update(json.load(fh))
            except Exception:
                pass

    def save(self, patch: dict):
        for k in _DEFAULTS:
            if k in patch:
                self.data[k] = patch[k]
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)

    def public(self) -> dict:
        """API 키는 보유 여부만 노출."""
        d = {k: v for k, v in self.data.items() if k != "api_key"}
        d["has_key"] = bool(self.data.get("api_key"))
        return d


def _http_json(url: str, payload: dict, headers: dict, timeout: int = 120) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(cfg: AIConfig, messages: list, system: str = "") -> dict:
    """messages: [{role, content}]. {ok, content, error} 반환."""
    p = cfg.data["provider"]
    try:
        if p == "ollama":
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            url = cfg.data["base"].rstrip("/") + "/api/chat"
            r = _http_json(url, {
                "model": cfg.data["model"], "messages": msgs, "stream": False,
                "options": {"temperature": cfg.data["temperature"]},
            }, {})
            return {"ok": True, "content": r.get("message", {}).get("content", "")}

        if p == "openai":
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            url = cfg.data["base"].rstrip("/") + "/v1/chat/completions"
            headers = {}
            if cfg.data.get("api_key"):
                headers["Authorization"] = "Bearer " + cfg.data["api_key"]
            r = _http_json(url, {
                "model": cfg.data["model"], "messages": msgs,
                "temperature": cfg.data["temperature"],
                "max_tokens": cfg.data["max_tokens"],
            }, headers)
            return {"ok": True, "content": r["choices"][0]["message"]["content"]}

        if p == "anthropic":
            base = cfg.data["base"] or "https://api.anthropic.com"
            if "11434" in base:            # 기본값이 ollama면 교정
                base = "https://api.anthropic.com"
            url = base.rstrip("/") + "/v1/messages"
            headers = {
                "x-api-key": cfg.data.get("api_key", ""),
                "anthropic-version": "2023-06-01",
            }
            r = _http_json(url, {
                "model": cfg.data["model"],
                "max_tokens": cfg.data["max_tokens"],
                "temperature": cfg.data["temperature"],
                "system": system or "",
                "messages": messages,
            }, headers)
            parts = [b.get("text", "") for b in r.get("content", []) if b.get("type") == "text"]
            return {"ok": True, "content": "".join(parts)}

        return {"ok": False, "error": f"알 수 없는 제공자: {p}"}

    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        hint = ""
        if p == "ollama":
            hint = " — Ollama가 켜져 있는지 확인하세요 (`ollama serve`)."
        return {"ok": False, "error": f"연결 실패: {reason}{hint}"}
    except Exception as e:
        return {"ok": False, "error": f"요청 실패: {e}"}
