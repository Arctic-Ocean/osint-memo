/* 격리 실행(샌드박스) 시험용 탐침. tests/test_sandbox.py 가 컴파일해 격리 안에서 돌린다.
 * 모든 결과는 "키=값" 한 줄씩 출력한다. 호스트의 어떤 것도 바꾸지 않는다. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <sys/prctl.h>
#include <sys/ptrace.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

extern char **environ;

static void line_of(const char *path, const char *key, const char *label) {
    FILE *f = fopen(path, "r");
    char b[512];
    if (!f) { printf("%s=ERR(%s)\n", label, strerror(errno)); return; }
    while (fgets(b, sizeof b, f))
        if (!strncmp(b, key, strlen(key))) {
            char *v = b + strlen(key);
            while (*v == ' ' || *v == '\t') v++;
            v[strcspn(v, "\n")] = 0;
            printf("%s=%s\n", label, v);
        }
    fclose(f);
}

static void try_open(const char *path, int flags) {
    int fd = open(path, flags, 0600);
    if (fd >= 0) { printf("open[%s]=OK\n", path); close(fd); }
    else printf("open[%s]=%s\n", path, strerror(errno));
}

static void list_dir(const char *path, const char *label) {
    DIR *d = opendir(path);
    if (!d) { printf("%s=ERR(%s)\n", label, strerror(errno)); return; }
    struct dirent *e; char buf[2048] = "";
    while ((e = readdir(d)))
        if (strcmp(e->d_name, ".") && strcmp(e->d_name, "..")) { strcat(buf, e->d_name); strcat(buf, ","); }
    closedir(d);
    printf("%s=%s\n", label, buf);
}

static int count_pids(void) {
    DIR *d = opendir("/proc"); int n = 0; struct dirent *e;
    if (!d) return -1;
    while ((e = readdir(d))) if (e->d_name[0] >= '0' && e->d_name[0] <= '9') n++;
    closedir(d);
    return n;
}

int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "";
    setvbuf(stdout, NULL, _IOLBF, 0);
    signal(SIGXFSZ, SIG_IGN);

    if (!strcmp(mode, "id")) {
        printf("uid=%d\ngid=%d\neuid=%d\n", getuid(), getgid(), geteuid());
        line_of("/proc/self/status", "NoNewPrivs:", "nonewprivs");
        line_of("/proc/self/status", "CapEff:", "capeff");
        line_of("/proc/self/status", "CapBnd:", "capbnd");
        line_of("/proc/self/status", "Seccomp:", "seccomp");
        char h[128]; gethostname(h, sizeof h); printf("hostname=%s\n", h);
        printf("pids=%d\n", count_pids());
        printf("pid=%d\n", getpid());
        int n = 0; for (char **e = environ; *e; e++) n++;
        printf("envcount=%d\n", n);
        for (char **e = environ; *e; e++) printf("env:%s\n", *e);
        return 0;
    }
    if (!strcmp(mode, "fs")) {
        for (int i = 2; i < argc; i++) try_open(argv[i], O_RDONLY);
        try_open("/usr/rework-w", O_WRONLY | O_CREAT);
        try_open("/etc/rework-w", O_WRONLY | O_CREAT);
        try_open("/rework-w", O_WRONLY | O_CREAT);
        try_open("/sandbox/rework-w", O_WRONLY | O_CREAT);
        try_open("/tmp/rework-w", O_WRONLY | O_CREAT);
        try_open("/dev/rework-w", O_WRONLY | O_CREAT);
        try_open("/dev/shm/rework-w", O_WRONLY | O_CREAT);
        try_open("/proc/rework-w", O_WRONLY | O_CREAT);
        try_open("/proc/sys/kernel/hostname", O_WRONLY);
        try_open("/proc/sysrq-trigger", O_WRONLY);
        list_dir("/", "ls:/");
        list_dir("/etc", "ls:/etc");
        list_dir("/sandbox", "ls:/sandbox");
        list_dir("/dev", "ls:/dev");
        char me[512]; ssize_t n = readlink("/proc/self/exe", me, sizeof me - 1);
        if (n > 0) { me[n] = 0; printf("exe=%s\n", me); try_open(me, O_WRONLY); }
        FILE *f = fopen("/etc/passwd", "r"); char b[256];
        if (f) { while (fgets(b, sizeof b, f)) printf("passwd:%s", b); fclose(f); }
        return 0;
    }
    if (!strcmp(mode, "fsro")) {            /* 대조군용: 아무것도 만들지 않고 읽기만 */
        for (int i = 2; i < argc; i++) try_open(argv[i], O_RDONLY);
        return 0;
    }
    if (!strcmp(mode, "create")) {          /* 주어진 경로에 파일 만들기를 시도(읽기 전용이어야 실패) */
        for (int i = 2; i < argc; i++) try_open(argv[i], O_WRONLY | O_CREAT);
        return 0;
    }
    if (!strcmp(mode, "net")) {
        int port = argc > 2 ? atoi(argv[2]) : 0;
        const char *abs_name = argc > 3 ? argv[3] : "";
        int s = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(port)};
        inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
        printf("connect_host_loopback=%s\n", connect(s, (void *)&a, sizeof a) ? strerror(errno) : "CONNECTED");
        close(s);
        s = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
        struct sockaddr_in e = {.sin_family = AF_INET, .sin_port = htons(53)};
        inet_pton(AF_INET, "1.1.1.1", &e.sin_addr);
        {
            int r = connect(s, (void *)&e, sizeof e);
            const char *res = r == 0 ? "CONNECTED" : strerror(errno);
            if (r < 0 && errno == EINPROGRESS) {      /* 호스트 네트워크일 때: 잠깐 기다려 결과만 본다 */
                struct pollfd { int fd; short ev, rev; } pf = {s, 4, 0};
                extern int poll(void *, unsigned long, int);
                poll(&pf, 1, 1500);
                int soerr = 0; socklen_t sl = sizeof soerr;
                getsockopt(s, SOL_SOCKET, SO_ERROR, &soerr, &sl);
                res = soerr == 0 ? "CONNECTED_OR_PENDING" : strerror(soerr);
            }
            printf("connect_external=%s\n", res);
        }
        close(s);
        s = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un u = {.sun_family = AF_UNIX};
        memcpy(u.sun_path + 1, abs_name, strlen(abs_name));
        printf("connect_host_abstract=%s\n",
               connect(s, (void *)&u, offsetof(struct sockaddr_un, sun_path) + 1 + strlen(abs_name))
                   ? strerror(errno) : "CONNECTED");
        close(s);
        s = socket(AF_PACKET, SOCK_RAW, 0);
        printf("af_packet=%s\n", s < 0 ? strerror(errno) : "OPENED");
        s = socket(AF_VSOCK, SOCK_STREAM, 0);
        printf("af_vsock=%s\n", s < 0 ? strerror(errno) : "OPENED");
        s = socket(AF_INET, SOCK_RAW, IPPROTO_ICMP);
        printf("raw_icmp=%s\n", s < 0 ? strerror(errno) : "OPENED");
        /* 샌드박스 자신의 루프백은 쓸 수 있어야 한다(자기들끼리 통신하는 프로그램) */
        int ls = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in l = {.sin_family = AF_INET, .sin_port = 0};
        inet_pton(AF_INET, "127.0.0.1", &l.sin_addr);
        int ok = !bind(ls, (void *)&l, sizeof l) && !listen(ls, 1);
        socklen_t ll = sizeof l;
        ok = ok && !getsockname(ls, (void *)&l, &ll);
        int cs = socket(AF_INET, SOCK_STREAM, 0);
        ok = ok && !connect(cs, (void *)&l, sizeof l);
        printf("own_loopback=%s\n", ok ? "OK" : strerror(errno));
        return 0;
    }
    if (!strcmp(mode, "sys")) {
        /* argv[2..]: 이름:번호 — 인자 0 으로 불러 errno 를 기록한다 */
        for (int i = 2; i < argc; i++) {
            char *c = strchr(argv[i], ':'); if (!c) continue;
            long nr = atol(c + 1); *c = 0;
            errno = 0;
            long r = syscall(nr, 0, 0, 0, 0, 0, 0);
            printf("sys:%s=%s\n", argv[i], r == -1 ? strerror(errno) : "RET");
        }
        errno = 0; int r = unshare(CLONE_NEWUSER);
        printf("unshare_newuser=%s\n", r ? strerror(errno) : "OK");
        errno = 0; r = unshare(CLONE_NEWNS);
        printf("unshare_newns=%s\n", r ? strerror(errno) : "OK");
        errno = 0; r = unshare(CLONE_NEWNET);
        printf("unshare_newnet=%s\n", r ? strerror(errno) : "OK");
        errno = 0; r = mount("none", "/tmp", "tmpfs", 0, NULL);
        printf("mount=%s\n", r ? strerror(errno) : "OK");
        errno = 0; r = chroot("/tmp");
        printf("chroot=%s\n", r ? strerror(errno) : "OK");
        errno = 0; long c3 = syscall(SYS_clone3, NULL, 0);
        printf("clone3=%s\n", c3 == -1 ? strerror(errno) : "RET");
        errno = 0; r = (int)syscall(SYS_clone, CLONE_NEWUSER | SIGCHLD, 0, 0, 0, 0);
        if (r == 0) _exit(0);
        printf("clone_newuser=%s\n", r < 0 ? strerror(errno) : "OK");
        errno = 0; r = fork();
        if (r == 0) _exit(0);
        if (r > 0) waitpid(r, 0, 0);
        printf("plain_fork=%s\n", r < 0 ? strerror(errno) : "OK");
        errno = 0; void *m = mmap(NULL, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
        printf("mmap_rwx=%s\n", m == MAP_FAILED ? strerror(errno) : "OK");
        return 0;
    }
    if (!strcmp(mode, "kill")) {
        for (int i = 2; i < argc; i++) {
            int pid = atoi(argv[i]); errno = 0;
            int r = kill(pid, 0);
            printf("kill[%d]=%s\n", pid, r ? strerror(errno) : "SENT");
        }
        return 0;
    }
    if (!strcmp(mode, "fds")) {
        DIR *d = opendir("/proc/self/fd"); struct dirent *e;
        while ((e = readdir(d))) {
            if (e->d_name[0] == '.') continue;
            char p[300], t[512]; snprintf(p, sizeof p, "/proc/self/fd/%s", e->d_name);
            ssize_t n = readlink(p, t, sizeof t - 1);
            if (n > 0) { t[n] = 0; printf("fd:%s=%s\n", e->d_name, t); }
        }
        return 0;
    }
    if (!strcmp(mode, "forks")) {
        int want = argc > 2 ? atoi(argv[2]) : 100, made = 0, err = 0;
        pid_t kids[8192];
        for (int i = 0; i < want && i < 8192; i++) {
            pid_t p = fork();
            if (p == 0) { for (;;) pause(); }
            if (p < 0) { err = errno; break; }
            kids[made++] = p;
        }
        printf("forked=%d\nfork_err=%s\n", made, err ? strerror(err) : "none");
        for (int i = 0; i < made; i++) kill(kids[i], SIGKILL);
        for (int i = 0; i < made; i++) waitpid(kids[i], 0, 0);
        return 0;
    }
    if (!strcmp(mode, "mem")) {
        long mb = argc > 2 ? atol(argv[2]) : 100;
        char *p = mmap(NULL, mb << 20, PROT_READ | PROT_WRITE, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
        if (p == MAP_FAILED) { printf("mem=%s\n", strerror(errno)); return 0; }
        for (long i = 0; i < (mb << 20); i += 4096) p[i] = 1;
        printf("mem=OK\n");
        return 0;
    }
    if (!strcmp(mode, "cpu")) { printf("cpu=spinning\n"); for (volatile unsigned long i = 0;; i++) {} }
    if (!strcmp(mode, "write")) {
        long mb = argc > 2 ? atol(argv[2]) : 1; const char *path = argc > 3 ? argv[3] : "/tmp/big";
        int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
        static char blk[1 << 20]; memset(blk, 'x', sizeof blk);
        long done = 0; int err = 0;
        for (long i = 0; i < mb; i++) { ssize_t n = write(fd, blk, sizeof blk); if (n != (ssize_t)sizeof blk) { err = errno; break; } done++; }
        printf("written_mb=%ld\nwrite_err=%s\n", done, err ? strerror(err) : "none");
        return 0;
    }
    if (!strcmp(mode, "open")) {
        int made = 0, err = 0;
        for (int i = 0; i < 100000; i++) { if (open("/dev/null", O_RDONLY) < 0) { err = errno; break; } made++; }
        printf("opened=%d\nopen_err=%s\n", made, err ? strerror(err) : "none");
        return 0;
    }
    if (!strcmp(mode, "daemon")) {
        /* 세션이 끝나도 살아남으려는 프로세스들(setsid·SIGTERM 무시). 정리가 이들을 모두 죽여야 한다 */
        for (int i = 0; i < 3; i++) {
            if (fork() == 0) {
                setsid(); signal(SIGTERM, SIG_IGN); signal(SIGHUP, SIG_IGN);
                if (fork() == 0) { for (;;) pause(); }
                for (;;) pause();
            }
        }
        printf("daemons=3\n");
        return 0;
    }
    if (!strcmp(mode, "gdbprobe")) {
        /* 대상이 같은 uid 의 gdb(디버거)나 샌드박스 init 을 조작해 호스트로 이어진 MI 파이프로 거짓 출력을 보낼 수 있는가 */
        pid_t pp = getppid();
        printf("ppid=%d pid=%d\n", pp, getpid());
        const char *forged = "~\"FORGED-BY-TARGET\\n\"\n*stopped,reason=\"exited-normally\"\n";
        int forged_ok = 0;
        for (int pid = 1; pid <= 2; pid++) {
            char path[64];
            for (int fd = 0; fd <= 2; fd++) {
                snprintf(path, sizeof path, "/proc/%d/fd/%d", pid, fd);
                char t[128]; ssize_t n = readlink(path, t, sizeof t - 1);
                printf("readlink[%d/fd/%d]=%s\n", pid, fd, n > 0 ? "OK" : strerror(errno));
                int w = open(path, O_WRONLY);
                printf("open_fd[%d/fd/%d]=%s\n", pid, fd, w >= 0 ? "OK" : strerror(errno));
                if (w >= 0) { if (write(w, forged, strlen(forged)) > 0) forged_ok = 1; close(w); }
            }
            snprintf(path, sizeof path, "/proc/%d/mem", pid);
            int m = open(path, O_RDWR);
            printf("open_mem[%d]=%s\n", pid, m >= 0 ? "OK" : strerror(errno));
            if (m >= 0) close(m);
            errno = 0; long r = ptrace(PTRACE_ATTACH, pid, 0, 0);
            printf("ptrace_attach[%d]=%s\n", pid, r == 0 ? "OK" : strerror(errno));
            if (r == 0) { int st; waitpid(pid, &st, 0); ptrace(PTRACE_DETACH, pid, 0, 0); }
            long pfd = syscall(SYS_pidfd_open, pid, 0);
            printf("pidfd_open[%d]=%s\n", pid, pfd >= 0 ? "OK" : strerror(errno));
            for (int fd = 0; fd <= 2 && pfd >= 0; fd++) {
                errno = 0; long st = syscall(SYS_pidfd_getfd, pfd, fd, 0);
                printf("pidfd_getfd[%d:%d]=%s\n", pid, fd, st >= 0 ? "STOLEN" : strerror(errno));
                if (st >= 0) { if (write((int)st, forged, strlen(forged)) > 0) forged_ok = 1; close((int)st); }
            }
            char buf[16]; struct iovec l = {buf, sizeof buf}, rm = {(void *)0x400000, sizeof buf};
            errno = 0; long vr = syscall(SYS_process_vm_readv, pid, &l, 1, &rm, 1, 0);
            printf("process_vm_readv[%d]=%s\n", pid, vr >= 0 ? "OK" : strerror(errno));
            errno = 0; long vw = syscall(SYS_process_vm_writev, pid, &l, 1, &rm, 1, 0);
            printf("process_vm_writev[%d]=%s\n", pid, vw >= 0 ? "OK" : strerror(errno));
        }
        printf("forged=%d\n", forged_ok);
        if (argc > 2 && !strcmp(argv[2], "stay")) sleep(30);   /* 거짓 보고가 통했다면 이 사이에 상태가 바뀐다 */
        return 0;
    }
    if (!strcmp(mode, "cpuforks")) {
        int n = argc > 2 ? atoi(argv[2]) : 4;
        for (int i = 0; i < n; i++) if (fork() == 0) { for (volatile unsigned long j = 0;; j++) {} }
        printf("cpuforks=%d\n", n);
        for (;;) pause();
    }
    if (!strcmp(mode, "memforks")) {
        int n = argc > 2 ? atoi(argv[2]) : 4; long mb = argc > 3 ? atol(argv[3]) : 60;
        for (int i = 0; i < n; i++)
            if (fork() == 0) {
                char *p = mmap(NULL, mb << 20, PROT_READ | PROT_WRITE, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
                if (p != MAP_FAILED) for (long k = 0; k < (mb << 20); k += 4096) p[k] = 1;
                for (;;) pause();
            }
        printf("memforks=%d x %ldMB\n", n, mb);
        for (;;) pause();
    }
    if (!strcmp(mode, "flood")) {
        long mb = argc > 2 ? atol(argv[2]) : 20; static char blk[1 << 16];
        memset(blk, 0, sizeof blk);                       /* NUL 바이트: JSON 으로는 6배(\u0000)로 부푼다 */
        for (long i = 0; i < mb * 16; i++) { if (write(1, blk, sizeof blk) < 0) break; }
        printf("\nflood-done\n");
        return 0;
    }
    if (!strcmp(mode, "killone")) {                  /* 샌드박스 pid 1(gdb)에 시그널을 보낸다 */
        int sig = argc > 2 ? atoi(argv[2]) : 15;
        errno = 0; int r = kill(1, sig);
        printf("kill1[%d]=%s\n", sig, r ? strerror(errno) : "SENT");
        sleep(30);
        return 0;
    }
    if (!strcmp(mode, "cpuburst")) {                 /* 아주 짧게 사는 자식을 계속 만들어 CPU 를 태운다(표본 사이에 숨으려는 시도) */
        int ms = argc > 2 ? atoi(argv[2]) : 40;
        printf("cpuburst=%d\n", ms);
        for (;;) {
            pid_t p = fork();
            if (p == 0) {
                struct timespec t0, t; clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t0);
                do { clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t); }
                while ((t.tv_sec - t0.tv_sec) * 1000 + (t.tv_nsec - t0.tv_nsec) / 1000000 < ms);
                _exit(0);
            }
            if (p < 0) { usleep(10000); continue; }
            waitpid(p, 0, 0);
        }
    }
    if (!strcmp(mode, "memcomm")) {                  /* 자식이 스스로 comm 을 gdb 로 바꾸고 메모리를 쓴다(메모리 감시 회피 시도) */
        int n = argc > 2 ? atoi(argv[2]) : 4; long mb = argc > 3 ? atol(argv[3]) : 60;
        for (int i = 0; i < n; i++)
            if (fork() == 0) {
                prctl(PR_SET_NAME, "gdb", 0, 0, 0);
                char *p = mmap(NULL, mb << 20, PROT_READ | PROT_WRITE, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
                if (p != MAP_FAILED) for (long k = 0; k < (mb << 20); k += 4096) p[k] = 1;
                for (;;) pause();
            }
        printf("memcomm=%d x %ldMB\n", n, mb);
        for (;;) pause();
    }
    if (!strcmp(mode, "cat")) {                      /* 파일 내용을 그대로 출력(노출 경로가 정말 그 대상인지 확인) */
        for (int i = 2; i < argc; i++) {
            int fd = open(argv[i], O_RDONLY);
            if (fd < 0) { printf("cat[%s]=ERR(%s)\n", argv[i], strerror(errno)); continue; }
            char b[512]; ssize_t n = read(fd, b, sizeof b - 1);
            if (n < 0) n = 0;
            b[n] = 0; for (char *q = b; *q; q++) if (*q == '\n') *q = ' ';
            printf("cat[%s]=%s\n", argv[i], b);
            close(fd);
        }
        return 0;
    }
    if (!strcmp(mode, "sleep")) { printf("sleeping\n"); sleep(1000); return 0; }
    if (!strcmp(mode, "tty")) {
        /* 대상의 표준 입출력이 전용 pty 여야 한다 */
        char *t = ttyname(0);
        printf("tty0=%s\ntty1=%s\n", t ? t : "none", ttyname(1) ? ttyname(1) : "none");
        char b[256];
        if (fgets(b, sizeof b, stdin)) printf("got:%s", b);
        return 0;
    }
    printf("unknown mode\n");
    return 2;
}
