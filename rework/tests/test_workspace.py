"""작업 폴더(소스 편집) 회귀 테스트 — 디스크에 쓰는 기능이므로 경로 탈출·덮어쓰기 사고를 중심으로 시험한다.

- 경로: `..`·절대 경로·NUL·**폴더 밖을 가리키는 심볼릭 링크**(읽기·쓰기·목록 모두)는 거부, 폴더 안의 링크는 허용
- 텍스트만: UTF-8·BOM·CP949·UTF-16 은 읽고 **같은 인코딩으로 저장**, 바이너리·너무 큰 파일은 거부
- 쓰기: 원자적(임시 파일이 남지 않음)·권한 유지·충돌 감지(mtime)·`.git`·rework 데이터 폴더·작업 폴더 자체는 금지
- 조작: 생성·이름 바꾸기·삭제(비어 있지 않은 폴더는 확인 필요, 링크는 링크만 삭제)
- 검색: 건너뛸 폴더·바이너리·큰 파일, 대소문자, 패턴, 개수 상한
- HTTP: 교차 출처 쓰기 차단·토큰·본문 상한·충돌의 JSON 응답

실행:  .venv/bin/python tests/test_workspace.py
"""
import http.client, json, os, shutil, socket, stat, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app import workspace as W                      # noqa: E402
from app.workspace import Workspace, WorkspaceError  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, "PASS" if ok else "FAIL", str(detail)))


def status(fn, *a, **kw):
    """fn 을 불러 (성공이면 0, WorkspaceError 면 그 status, 다른 예외면 -1) 과 결과·예외."""
    try:
        return 0, fn(*a, **kw), None
    except WorkspaceError as e:
        return e.status, None, e
    except Exception as e:                           # noqa: BLE001
        return -1, None, e


def mk_tree():
    base = tempfile.mkdtemp(prefix="rework-ws-")
    root = os.path.join(base, "proj")
    secret = os.path.join(base, "secret")
    os.makedirs(os.path.join(root, "src"))
    os.makedirs(os.path.join(root, ".git"))
    os.makedirs(secret)
    open(os.path.join(secret, "key.txt"), "w").write("TOP-SECRET")
    open(os.path.join(root, "src", "a.c"), "w").write("int main(){return 0;}\n")
    open(os.path.join(root, ".git", "config"), "w").write("[core]\n")
    return base, root, secret


def basic_section():
    base, root, secret = mk_tree()
    data_dir = os.path.join(base, "data")
    os.makedirs(data_dir)
    open(os.path.join(data_dir, "ai.json"), "w").write("{}")
    ws = Workspace(protected=lambda: [data_dir])
    try:
        # ---- 열기 ----
        check("열기: 없는 폴더는 404", status(ws.open, os.path.join(base, "nope"))[0] == 404)
        check("열기: 파일은 400", status(ws.open, os.path.join(root, "src", "a.c"))[0] == 400)
        check("열기: 최상위(/)는 거부", status(ws.open, "/")[0] == 400)
        check("열기 전에는 모든 동작이 400", status(ws.listdir, "")[0] == 400 and status(ws.read, "a")[0] == 400)
        ws.open(root)
        check("열기: 정상", ws.root == os.path.realpath(root))

        # ---- 경로 탈출 ----
        bad = ["../secret/key.txt", "src/../../secret/key.txt", "/etc/passwd", "a\0b", "src/..", ".."]
        res = {b: status(ws.read, b)[0] for b in bad}
        check("경로: `..`·절대 경로·NUL 은 400(읽기)", all(v == 400 for v in res.values()), res)
        res = {b: status(ws.write, b, "x")[0] for b in bad}
        check("경로: 같은 경로로 쓰기도 400", all(v == 400 for v in res.values()), res)
        os.symlink(os.path.join(secret, "key.txt"), os.path.join(root, "evil_file"))
        os.symlink(secret, os.path.join(root, "evil_dir"))
        os.symlink(os.path.join(root, "src", "a.c"), os.path.join(root, "good_link"))
        s1 = status(ws.read, "evil_file")
        s2 = status(ws.read, "evil_dir/key.txt")
        s3 = status(ws.listdir, "evil_dir")
        s4 = status(ws.write, "evil_file", "PWNED")
        s5 = status(ws.create, "evil_dir/new.txt", "file")
        check("심볼릭 링크: 폴더 밖을 가리키면 읽기·목록·쓰기·만들기 모두 403", [s1[0], s2[0], s3[0], s4[0], s5[0]] == [403] * 5, [s1[0], s2[0], s3[0], s4[0], s5[0]])
        check("심볼릭 링크: 밖의 파일은 바뀌지 않았다", open(os.path.join(secret, "key.txt")).read() == "TOP-SECRET" and not os.path.exists(os.path.join(secret, "new.txt")))
        check("심볼릭 링크: 폴더 안을 가리키는 링크는 읽을 수 있다", status(ws.read, "good_link")[0] == 0)

        # ---- 목록 ----
        os.mkdir(os.path.join(root, "Zdir"))
        open(os.path.join(root, "b.txt"), "w").write("b")
        open(os.path.join(os.fsencode(root), b"bad_\xb0\xa1.txt"), "wb").write(b"x")       # JSON 으로 낼 수 없는 이름
        ls = ws.listdir("")
        names = [e["name"] for e in ls["entries"]]
        dirs_first = [e["dir"] for e in ls["entries"]]
        check("목록: 폴더가 먼저·이름순", dirs_first == sorted(dirs_first, reverse=True) and names.index("src") < names.index("b.txt"), names)
        check("목록: 비 UTF-8 이름은 건너뜀", not any("bad_" in n for n in names))
        by = {e["name"]: e for e in ls["entries"]}
        check("목록: 밖을 가리키는 링크는 outside 표시·들어갈 수 없음(dir=false)", by["evil_dir"]["outside"] is True and by["evil_dir"]["dir"] is False and by["good_link"]["link"] is True, by["evil_dir"])
        old = W.MAX_DIR_ENTRIES
        W.MAX_DIR_ENTRIES = 3
        try:
            check("목록: 항목 수 상한이면 truncated", ws.listdir("")["truncated"] is True)
        finally:
            W.MAX_DIR_ENTRIES = old

        # ---- 읽기·인코딩 ----
        def put(name, raw):
            with open(os.path.join(root, name), "wb") as fh:
                fh.write(raw)
        put("u8.txt", "한글 UTF-8\n".encode("utf-8"))
        put("bom.txt", b"\xef\xbb\xbf" + "BOM 한글".encode("utf-8"))
        put("k.txt", "한글 CP949 소스\r\n".encode("cp949"))
        put("w16.txt", "UTF-16 한글".encode("utf-16"))
        put("bin.dat", b"\x7fELF\0\0\0\1\2\3")
        put("bad.txt", b"\xff\xff\xff\xff")
        put("empty.txt", b"")
        r = {n: status(ws.read, n) for n in ("u8.txt", "bom.txt", "k.txt", "w16.txt", "empty.txt")}
        encs = {n: (v[1]["encoding"] if v[0] == 0 else v[0]) for n, v in r.items()}
        check("읽기: UTF-8·BOM·CP949·UTF-16·빈 파일의 인코딩을 알아낸다", encs == {"u8.txt": "utf-8", "bom.txt": "utf-8-sig", "k.txt": "cp949", "w16.txt": "utf-16", "empty.txt": "utf-8"}, encs)
        check("읽기: 한글이 깨지지 않는다", r["k.txt"][1]["text"] == "한글 CP949 소스\r\n" and r["w16.txt"][1]["text"] == "UTF-16 한글" and r["bom.txt"][1]["text"] == "BOM 한글")
        sb, _, eb = status(ws.read, "bin.dat")
        sb2 = status(ws.read, "bad.txt")[0]
        check("읽기: 바이너리(NUL)·해석 불가는 415(binary 표시)", sb == 415 and eb.extra.get("binary") is True and sb2 == 415, (sb, sb2))
        old = W.MAX_TEXT_BYTES
        W.MAX_TEXT_BYTES = 10
        try:
            check("읽기: 크기 상한 초과는 413", status(ws.read, "u8.txt")[0] == 413)
            check("쓰기: 크기 상한 초과는 413", status(ws.write, "u8.txt", "x" * 100)[0] == 413)
        finally:
            W.MAX_TEXT_BYTES = old
        check("읽기: 폴더는 400·없는 파일은 404", status(ws.read, "src")[0] == 400 and status(ws.read, "nope.txt")[0] == 404)

        # ---- 쓰기 ----
        r = ws.read("k.txt")
        out = ws.write("k.txt", "수정한 CP949 소스\r\n", r["encoding"], r["mtime_ns"])
        on_disk = open(os.path.join(root, "k.txt"), "rb").read()
        check("쓰기: 읽은 인코딩(CP949)·줄바꿈 그대로 저장", on_disk == "수정한 CP949 소스\r\n".encode("cp949"), on_disk[:20])
        check("쓰기: 임시 파일이 남지 않는다", not [n for n in os.listdir(root) if n.endswith(".tmp")], os.listdir(root))
        os.chmod(os.path.join(root, "b.txt"), 0o755)
        ws.write("b.txt", "new", "utf-8")
        check("쓰기: 파일 권한 유지", stat.S_IMODE(os.stat(os.path.join(root, "b.txt")).st_mode) == 0o755, oct(os.stat(os.path.join(root, "b.txt")).st_mode))
        e1 = status(ws.write, "k.txt", "한글ⓧ😀", "cp949")
        check("쓰기: 그 인코딩으로 표현 못 하는 문자는 400(encoding_error)", e1[0] == 400 and e1[2].extra.get("encoding_error") is True and open(os.path.join(root, "k.txt"), "rb").read() == "수정한 CP949 소스\r\n".encode("cp949"))
        check("쓰기: 지원하지 않는 인코딩·문자열 아님은 400", status(ws.write, "k.txt", "x", "ebcdic")[0] == 400 and status(ws.write, "k.txt", 123)[0] == 400)
        # 충돌
        r = ws.read("src/a.c")
        time.sleep(0.02)
        with open(os.path.join(root, "src", "a.c"), "w") as fh:
            fh.write("// 다른 프로그램이 고침\n")
        c = status(ws.write, "src/a.c", "내 편집", "utf-8", r["mtime_ns"])
        check("충돌: 읽은 뒤 디스크가 바뀌었으면 409(conflict·현재 mtime)", c[0] == 409 and c[2].extra.get("conflict") is True and "mtime_ns" in c[2].extra
              and open(os.path.join(root, "src", "a.c")).read().startswith("// 다른"), c[0])
        f = status(ws.write, "src/a.c", "내 편집", "utf-8", r["mtime_ns"], True)
        check("충돌: 덮어쓰기(force)는 저장됨", f[0] == 0 and open(os.path.join(root, "src", "a.c")).read() == "내 편집")
        # 금지 구역
        g1 = status(ws.write, ".git/config", "[evil]")
        g2 = status(ws.create, ".git/hooks", "dir")
        g3 = status(ws.rename, ".git/config", "cfg2")
        g4 = status(ws.delete, ".git/config")
        check("금지: .git 안의 쓰기·만들기·이름 바꾸기·삭제는 403", [g1[0], g2[0], g3[0], g4[0]] == [403] * 4 and open(os.path.join(root, ".git", "config")).read() == "[core]\n", [g1[0], g2[0], g3[0], g4[0]])
        d1 = status(ws.write, "src", "x")
        check("쓰기: 폴더에는 쓸 수 없다", d1[0] == 400, d1[0])
        # 보호된 데이터 폴더가 작업 폴더 안에 있는 경우
        nested = os.path.join(root, "datadir")
        os.makedirs(nested)
        open(os.path.join(nested, "ai.json"), "w").write("{}")
        ws2 = Workspace(protected=lambda: [nested])
        ws2.open(root)
        p1 = status(ws2.write, "datadir/ai.json", "{\"api_key\":\"x\"}")
        p2 = status(ws2.delete, "datadir", True)
        check("금지: rework 데이터 폴더(작업 폴더 안에 있어도)는 수정·삭제 불가", p1[0] == 403 and p2[0] == 403 and open(os.path.join(nested, "ai.json")).read() == "{}", [p1[0], p2[0]])
        check("금지: 작업 폴더 자체는 삭제·이름 변경 불가", status(ws.delete, "")[0] == 403 and status(ws.rename, "", "x")[0] in (400, 403))

        # ---- 생성·이름 바꾸기·삭제 ----
        check("생성: 파일·폴더", status(ws.create, "src/new.c", "file")[0] == 0 and status(ws.create, "src/sub", "dir")[0] == 0
              and os.path.isfile(os.path.join(root, "src", "new.c")) and os.path.isdir(os.path.join(root, "src", "sub")))
        check("생성: 이미 있으면 409·없는 상위는 404", status(ws.create, "src/new.c", "file")[0] == 409 and status(ws.create, "nodir/x.c", "file")[0] == 404)
        names = ["", ".", "..", "a/b\0", "x" * 300, "ctl\x01"]
        bad_names = {n: status(ws.create, "src/" + n, "file")[0] for n in ("..", "x" * 300, "ctl\x01", "a\0b")}
        check("생성: 이상한 이름은 400", all(v == 400 for v in bad_names.values()), bad_names)
        check("이름 바꾸기: 정상·대상 존재 409·밖으로 400", status(ws.rename, "src/new.c", "src/renamed.c")[0] == 0 and os.path.isfile(os.path.join(root, "src", "renamed.c"))
              and status(ws.rename, "src/renamed.c", "src/a.c")[0] == 409 and status(ws.rename, "src/renamed.c", "../out.c")[0] == 400)
        check("이름 바꾸기: 폴더를 자기 안으로는 400", status(ws.rename, "src", "src/sub/src")[0] == 400)
        full = os.path.join(root, "tree")
        os.makedirs(os.path.join(full, "deep"))
        open(os.path.join(full, "deep", "f"), "w").write("x")
        d_ne = status(ws.delete, "tree")
        check("삭제: 비어 있지 않은 폴더는 확인(409 not_empty) 없이는 안 지움", d_ne[0] == 409 and d_ne[2].extra.get("not_empty") and os.path.isdir(full))
        check("삭제: recursive 확인 뒤에는 지움", status(ws.delete, "tree", True)[0] == 0 and not os.path.exists(full))
        os.symlink(secret, os.path.join(root, "linkdel"))
        status(ws.delete, "linkdel", True)
        check("삭제: 링크는 링크만 지우고 대상(폴더 밖)은 그대로", not os.path.lexists(os.path.join(root, "linkdel")) and os.path.isfile(os.path.join(secret, "key.txt")))
        os.mkdir(os.path.join(root, "dlink_target"))
        open(os.path.join(root, "dlink_target", "keep"), "w").write("k")
        os.symlink(os.path.join(root, "dlink_target"), os.path.join(root, "dlink"))
        status(ws.delete, "dlink", True)
        check("삭제: 폴더 안을 가리키는 링크도 링크만", not os.path.lexists(os.path.join(root, "dlink")) and os.path.isfile(os.path.join(root, "dlink_target", "keep")))
        os.makedirs(os.path.join(root, "rm_with_link"))
        os.symlink(secret, os.path.join(root, "rm_with_link", "inner_link"))
        status(ws.delete, "rm_with_link", True)
        check("삭제: 안에 밖을 가리키는 링크가 든 폴더를 지워도 대상은 그대로", not os.path.exists(os.path.join(root, "rm_with_link")) and os.path.isfile(os.path.join(secret, "key.txt")))

        # ---- 검색 ----
        sroot = os.path.join(base, "sproj")
        os.makedirs(os.path.join(sroot, "node_modules"))
        os.makedirs(os.path.join(sroot, ".git"))
        os.makedirs(os.path.join(sroot, "src"))
        open(os.path.join(sroot, "src", "a.c"), "w").write("int Needle = 1;\nint other;\n// needle again\n")
        open(os.path.join(sroot, "src", "b.py"), "w").write("needle = 2\n")
        open(os.path.join(sroot, "node_modules", "x.js"), "w").write("needle\n")
        open(os.path.join(sroot, ".git", "o"), "w").write("needle\n")
        open(os.path.join(sroot, "big.txt"), "w").write("needle " + "x" * (W.SEARCH_MAX_FILE + 10))
        open(os.path.join(sroot, "bin.dat"), "wb").write(b"needle\0\1\2")
        os.symlink(secret, os.path.join(sroot, "linkdir"))
        open(os.path.join(sroot, "k.c"), "wb").write("한글 needle 소스\n".encode("cp949"))
        sws = Workspace()
        sws.open(sroot)
        r = sws.search("needle")
        paths = sorted({x["path"] for x in r["results"]})
        check("검색: 건너뛸 폴더(.git·node_modules)·바이너리·큰 파일·링크 폴더 제외", paths == ["k.c", "src/a.c", "src/b.py"], paths)
        check("검색: 대소문자 구분 옵션", len(sws.search("Needle", True)["results"]) == 1 and len(sws.search("Needle", False)["results"]) >= 4)
        check("검색: 파일 패턴(*.py)", [x["path"] for x in sws.search("needle", False, "*.py")["results"]] == ["src/b.py"])
        check("검색: CP949 파일도 찾는다·줄/열 번호", any(x["path"] == "k.c" and x["line"] == 1 and x["col"] == 4 for x in r["results"]), [x for x in r["results"] if x["path"] == "k.c"])
        old = W.SEARCH_MAX_RESULTS
        W.SEARCH_MAX_RESULTS = 2
        try:
            rr = sws.search("needle")
            check("검색: 결과 상한이면 truncated", len(rr["results"]) == 2 and rr["truncated"] is True)
        finally:
            W.SEARCH_MAX_RESULTS = old
        check("검색: 빈 검색어·너무 긴 검색어는 400", status(sws.search, "")[0] == 400 and status(sws.search, "x" * 201)[0] == 400)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_section():
    base = tempfile.mkdtemp(prefix="rework-wshttp-")
    data = os.path.join(base, "data")
    work = os.path.join(base, "work")
    os.makedirs(work)
    open(os.path.join(work, "a.c"), "w").write("int x;\n")
    os.makedirs(os.path.join(work, ".git"))
    open(os.path.join(work, ".git", "config"), "w").write("[core]\n")
    py = os.path.join(ROOT, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    port = free_port()
    tok = "ws-tok-" + os.urandom(6).hex()
    proc = subprocess.Popen([py, os.path.join(ROOT, "run.py"), "--port", str(port)], env={**os.environ, "REWORK_DATA": data, "REWORK_TOKEN": tok},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def req(method, path, body=None, headers=None, raw=None):
        h = {"X-Rework-Token": tok}
        h.update(headers or {})
        data_ = raw
        if body is not None:
            data_ = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        c.request(method, path, body=data_, headers=h)
        r = c.getresponse()
        out = r.read()
        hdr = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        try:
            return r.status, json.loads(out), hdr
        except ValueError:
            return r.status, out, hdr
    try:
        t0 = time.time()
        while time.time() - t0 < 25:
            try:
                if req("GET", "/api/ws")[0] == 200:
                    break
            except OSError:
                time.sleep(0.2)
        st, j, _ = req("GET", "/api/ws")
        check("HTTP: 처음엔 열린 폴더가 없다", st == 200 and j["root"] is None, j)
        st, j, _ = req("POST", "/api/ws/open", {"path": work})
        check("HTTP: 폴더 열기·최근 목록 기록", st == 200 and j["root"]["name"] == "work" and work in j["recent"], j)
        check("HTTP: 최근 목록 파일은 0600", os.stat(os.path.join(data, "workspace.json")).st_mode & 0o777 == 0o600)
        st, j, _ = req("GET", "/api/ws/tree?path=")
        check("HTTP: 트리", st == 200 and {"a.c", ".git"} <= {e["name"] for e in j["entries"]}, j)
        st, j, _ = req("GET", "/api/ws/file?path=a.c")
        mt = j["mtime_ns"]
        st2, j2, _ = req("PUT", "/api/ws/file", {"path": "a.c", "text": "int y;\n", "encoding": "utf-8", "expected_mtime_ns": mt})
        check("HTTP: 읽기·저장", st == 200 and st2 == 200 and open(os.path.join(work, "a.c")).read() == "int y;\n", (st, st2))
        st, j, hdr = req("PUT", "/api/ws/file", {"path": "a.c", "text": "z", "expected_mtime_ns": mt})
        check("HTTP: 충돌은 409 JSON(conflict·mtime_ns) + 보안 헤더", st == 409 and j.get("conflict") is True and "mtime_ns" in j and hdr.get("x-frame-options") == "DENY", (st, j))
        st, j, _ = req("PUT", "/api/ws/file", {"path": ".git/config", "text": "[evil]"})
        check("HTTP: .git 쓰기는 403", st == 403 and "git" in j["detail"], j)
        st, j, _ = req("GET", "/api/ws/file?path=../../etc/passwd")
        check("HTTP: 경로 탈출은 400", st == 400, st)
        st, j, _ = req("PUT", "/api/ws/file", {"path": "a.c", "text": "x", "expected_mtime_ns": "abc"})
        check("HTTP: 잘못된 필드 형식은 400(500 아님)", st == 400, st)
        st, j, _ = req("POST", "/api/ws/op", {"op": "create", "path": "n.c", "kind": "file"})
        st2, j2, _ = req("POST", "/api/ws/op", {"op": "rename", "path": "n.c", "to": "m.c"})
        st3, j3, _ = req("POST", "/api/ws/op", {"op": "delete", "path": "m.c"})
        st4, j4, _ = req("POST", "/api/ws/op", {"op": "explode", "path": "m.c"})
        check("HTTP: 만들기·이름 바꾸기·삭제·알 수 없는 op(400)", [st, st2, st3, st4] == [200, 200, 200, 400] and not os.path.exists(os.path.join(work, "m.c")), [st, st2, st3, st4])
        st, j, _ = req("GET", "/api/ws/stat?path=a.c")
        check("HTTP: stat(내용 없이 mtime·크기)", st == 200 and j["size"] == 7 and int(j["mtime_ns"]) > 0 and j["dir"] is False, j)
        check("HTTP: mtime_ns 는 문자열로 오가고(자바스크립트 정밀도) 정수·잘못된 형식도 안전하게 처리",
              isinstance(j["mtime_ns"], str) and int(j["mtime_ns"]) > 2 ** 53
              and req("PUT", "/api/ws/file", {"path": "a.c", "text": "int y;\n", "expected_mtime_ns": int(j["mtime_ns"])})[0] == 200
              and req("PUT", "/api/ws/file", {"path": "a.c", "text": "int y;\n", "expected_mtime_ns": "1" * 40})[0] == 400
              and req("PUT", "/api/ws/file", {"path": "a.c", "text": "int y;\n", "expected_mtime_ns": [1]})[0] == 400
              and req("PUT", "/api/ws/file", {"path": "a.c", "text": "int y;\n", "expected_mtime_ns": True})[0] == 400, j)
        st, j, _ = req("GET", "/api/ws/search?q=int")
        check("HTTP: 검색", st == 200 and any(x["path"] == "a.c" for x in j["results"]), j)
        # CSRF·토큰
        evil = {"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"}
        a = req("PUT", "/api/ws/file", {"path": "a.c", "text": "PWNED"}, evil)[0]
        b = req("GET", "/api/ws/file?path=a.c", None, evil)[0]
        c = req("POST", "/api/ws/open", {"path": base}, {"Origin": f"http://127.0.0.1:{port + 1}"})[0]
        d = req("PUT", "/api/ws/file", {"path": "a.c", "text": "PWNED"}, {"X-Rework-Token": "wrong"})[0]
        check("HTTP: 교차 출처 쓰기·읽기·폴더 열기는 403, 틀린 토큰은 401", [a, b, c, d] == [403, 403, 403, 401] and open(os.path.join(work, "a.c")).read() == "int y;\n", [a, b, c, d])
        big = json.dumps({"path": "a.c", "text": "x" * (17 * 1024 * 1024)}).encode()
        try:
            st, j, _ = req("PUT", "/api/ws/file", raw=big, headers={"Content-Type": "application/json"})
        except OSError:
            st = 413                                         # 서버가 본문을 다 받기 전에 끊었다
        check("HTTP: 16MB 를 넘는 요청 본문은 413(파일은 그대로)", st == 413 and open(os.path.join(work, "a.c")).read() == "int y;\n", st)
        st, j, _ = req("POST", "/api/ws/open", {"path": data})
        st2, j2, _ = req("PUT", "/api/ws/file", {"path": "x", "text": "y"})
        check("HTTP: 데이터 폴더를 작업 폴더로 열어도 그 안의 파일은 수정할 수 없다", st == 200, st)
        if st == 200:
            st3, j3, _ = req("GET", "/api/ws/tree?path=")
            names = [e["name"] for e in j3["entries"]]
            target = "workspace.json" if "workspace.json" in names else (names[0] if names else None)
            st4, j4, _ = req("PUT", "/api/ws/file", {"path": target, "text": "{}"}) if target else (0, {}, {})
            check("HTTP: …쓰기는 403", st4 == 403, (target, st4))
        st, j, _ = req("POST", "/api/ws/close")
        check("HTTP: 닫기", st == 200 and j["root"] is None)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(base, ignore_errors=True)


def main():
    for fn in (basic_section, http_section):
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            import traceback
            check(fn.__name__, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}")
    w = max(len(n) for n, _, _ in RESULTS)
    bad = 0
    for n, s, d in RESULTS:
        print(f"{s:4} {n:<{w}}  {d[:200]}")
        bad += s != "PASS"
    print(f"\n== {len(RESULTS) - bad}/{len(RESULTS)} 통과 ==")
    return bad


def test_workspace_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
