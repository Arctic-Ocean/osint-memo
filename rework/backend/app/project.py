"""프로젝트 저장소.

분석가가 붙인 주석(이름 변경·코멘트·북마크)을 SQLite에 보존한다.
바이너리의 sha256별로 분리되므로, 같은 파일을 다시 열면 작업이 복원된다.
저장 위치는 데이터 디렉터리 하위 projects/<sha256>.sqlite.
"""
from __future__ import annotations

import os
import sqlite3
import time


class Project:
    def __init__(self, db_path: str, sha256: str, meta: dict | None = None):
        self.sha256 = sha256
        first = not os.path.isfile(db_path)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_schema()
        if first and meta:
            self.set_meta(meta)
        # 캐시
        self._names: dict[int, str] = {}
        self._comments: dict[int, str] = {}
        self._reload_cache()

    def _init_schema(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS names (
                addr INTEGER PRIMARY KEY, name TEXT NOT NULL, ts REAL
            );
            CREATE TABLE IF NOT EXISTS comments (
                addr INTEGER PRIMARY KEY, text TEXT NOT NULL, ts REAL
            );
            CREATE TABLE IF NOT EXISTS bookmarks (
                addr INTEGER PRIMARY KEY, label TEXT, ts REAL
            );
            CREATE TABLE IF NOT EXISTS varnames (
                func INTEGER, slot TEXT, name TEXT, ts REAL,
                PRIMARY KEY (func, slot)
            );
            """
        )
        self.db.commit()

    def _reload_cache(self):
        self._names = {r["addr"]: r["name"] for r in self.db.execute("SELECT addr,name FROM names")}
        self._comments = {r["addr"]: r["text"] for r in self.db.execute("SELECT addr,text FROM comments")}

    # ---- meta ----
    def set_meta(self, meta: dict):
        for k, v in meta.items():
            self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
        self.db.commit()

    def get_meta(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    # ---- names (리네임) ----
    def set_name(self, addr: int, name: str):
        name = (name or "").strip()
        if not name:
            self.db.execute("DELETE FROM names WHERE addr=?", (addr,))
            self._names.pop(addr, None)
        else:
            self.db.execute("INSERT OR REPLACE INTO names(addr,name,ts) VALUES(?,?,?)",
                            (addr, name, time.time()))
            self._names[addr] = name
        self.db.commit()

    def get_name(self, addr: int):
        return self._names.get(addr)

    def all_names(self) -> dict:
        return dict(self._names)

    # ---- comments ----
    def set_comment(self, addr: int, text: str):
        text = text or ""
        if not text.strip():
            self.db.execute("DELETE FROM comments WHERE addr=?", (addr,))
            self._comments.pop(addr, None)
        else:
            self.db.execute("INSERT OR REPLACE INTO comments(addr,text,ts) VALUES(?,?,?)",
                            (addr, text, time.time()))
            self._comments[addr] = text
        self.db.commit()

    def get_comment(self, addr: int):
        return self._comments.get(addr)

    # ---- bookmarks ----
    def toggle_bookmark(self, addr: int, label: str = "") -> bool:
        exist = self.db.execute("SELECT 1 FROM bookmarks WHERE addr=?", (addr,)).fetchone()
        if exist:
            self.db.execute("DELETE FROM bookmarks WHERE addr=?", (addr,))
            self.db.commit()
            return False
        self.db.execute("INSERT INTO bookmarks(addr,label,ts) VALUES(?,?,?)",
                        (addr, label, time.time()))
        self.db.commit()
        return True

    def bookmarks(self) -> list:
        return [{"addr": r["addr"], "label": r["label"]}
                for r in self.db.execute("SELECT addr,label FROM bookmarks ORDER BY addr")]

    # ---- varnames (함수-지역 변수 리네임) ----
    def set_varname(self, func: int, slot: str, name: str):
        name = (name or "").strip()
        if not name:
            self.db.execute("DELETE FROM varnames WHERE func=? AND slot=?", (func, slot))
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO varnames(func,slot,name,ts) VALUES(?,?,?,?)",
                (func, slot, name, time.time()))
        self.db.commit()

    def get_varnames(self, func: int) -> dict:
        return {r["slot"]: r["name"]
                for r in self.db.execute("SELECT slot,name FROM varnames WHERE func=?", (func,))}

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass
