"""libc·WinAPI 함수 원형 표: 호출 인자 수와 반환형, 인자 형식(문자열 포인터 등), 돌아오지 않는 함수.

각 항목: 이름 → (반환형, [인자형...], 가변인자 여부). 형식은 C 표기 문자열이고 types.py 가 해석한다."""

NORETURN = {"exit", "_exit", "_Exit", "abort", "__stack_chk_fail", "__assert_fail", "__assert_rtn", "longjmp", "_longjmp", "siglongjmp",
            "pthread_exit", "__cxa_throw", "__cxa_rethrow", "err", "errx", "verr", "verrx", "quick_exit", "ExitProcess",
            "TerminateProcess_", "__fortify_fail", "__chk_fail", "__libc_fatal", "terminate", "_ZSt9terminatev", "abort_",
            "__builtin_unreachable", "__ubsan_handle_builtin_unreachable", "RtlExitUserProcess", "ExitThread"}

_P = {
    # stdio
    "printf": ("int", ["const char *"], True),
    "fprintf": ("int", ["FILE *", "const char *"], True),
    "sprintf": ("int", ["char *", "const char *"], True),
    "snprintf": ("int", ["char *", "size_t", "const char *"], True),
    "dprintf": ("int", ["int", "const char *"], True),
    "vprintf": ("int", ["const char *", "va_list"], False),
    "vfprintf": ("int", ["FILE *", "const char *", "va_list"], False),
    "vsprintf": ("int", ["char *", "const char *", "va_list"], False),
    "vsnprintf": ("int", ["char *", "size_t", "const char *", "va_list"], False),
    "__printf_chk": ("int", ["int", "const char *"], True),
    "__fprintf_chk": ("int", ["FILE *", "int", "const char *"], True),
    "__sprintf_chk": ("int", ["char *", "int", "size_t", "const char *"], True),
    "__snprintf_chk": ("int", ["char *", "size_t", "int", "size_t", "const char *"], True),
    "scanf": ("int", ["const char *"], True),
    "fscanf": ("int", ["FILE *", "const char *"], True),
    "sscanf": ("int", ["const char *", "const char *"], True),
    "__isoc99_scanf": ("int", ["const char *"], True),
    "__isoc99_fscanf": ("int", ["FILE *", "const char *"], True),
    "__isoc99_sscanf": ("int", ["const char *", "const char *"], True),
    "puts": ("int", ["const char *"], False),
    "putchar": ("int", ["int"], False),
    "putc": ("int", ["int", "FILE *"], False),
    "fputc": ("int", ["int", "FILE *"], False),
    "fputs": ("int", ["const char *", "FILE *"], False),
    "getchar": ("int", [], False),
    "getc": ("int", ["FILE *"], False),
    "fgetc": ("int", ["FILE *"], False),
    "gets": ("char *", ["char *"], False),
    "fgets": ("char *", ["char *", "int", "FILE *"], False),
    "fopen": ("FILE *", ["const char *", "const char *"], False),
    "fclose": ("int", ["FILE *"], False),
    "fread": ("size_t", ["void *", "size_t", "size_t", "FILE *"], False),
    "fwrite": ("size_t", ["const void *", "size_t", "size_t", "FILE *"], False),
    "fflush": ("int", ["FILE *"], False),
    "fseek": ("int", ["FILE *", "long", "int"], False),
    "ftell": ("long", ["FILE *"], False),
    "rewind": ("void", ["FILE *"], False),
    "perror": ("void", ["const char *"], False),
    # math
    "sqrt": ("double", ["double"], False), "sqrtf": ("float", ["float"], False), "fabs": ("double", ["double"], False),
    "fabsf": ("float", ["float"], False), "floor": ("double", ["double"], False), "floorf": ("float", ["float"], False),
    "ceil": ("double", ["double"], False), "ceilf": ("float", ["float"], False), "round": ("double", ["double"], False),
    "trunc": ("double", ["double"], False), "sin": ("double", ["double"], False), "cos": ("double", ["double"], False),
    "tan": ("double", ["double"], False), "asin": ("double", ["double"], False), "acos": ("double", ["double"], False),
    "atan": ("double", ["double"], False), "exp": ("double", ["double"], False), "log": ("double", ["double"], False),
    "log10": ("double", ["double"], False), "log2": ("double", ["double"], False), "sinf": ("float", ["float"], False),
    "cosf": ("float", ["float"], False), "expf": ("float", ["float"], False), "logf": ("float", ["float"], False),
    "pow": ("double", ["double", "double"], False), "atan2": ("double", ["double", "double"], False),
    "fmod": ("double", ["double", "double"], False), "hypot": ("double", ["double", "double"], False),
    "fmin": ("double", ["double", "double"], False), "fmax": ("double", ["double", "double"], False),
    "powf": ("float", ["float", "float"], False), "atan2f": ("float", ["float", "float"], False),
    "atof": ("double", ["const char *"], False), "strtod": ("double", ["const char *", "char **"], False),
    "strtof": ("float", ["const char *", "char **"], False), "ldexp": ("double", ["double", "int"], False),
    "remove": ("int", ["const char *"], False),
    "rename": ("int", ["const char *", "const char *"], False),
    # string / memory
    "strlen": ("size_t", ["const char *"], False),
    "strnlen": ("size_t", ["const char *", "size_t"], False),
    "strcpy": ("char *", ["char *", "const char *"], False),
    "strncpy": ("char *", ["char *", "const char *", "size_t"], False),
    "strcat": ("char *", ["char *", "const char *"], False),
    "strncat": ("char *", ["char *", "const char *", "size_t"], False),
    "strcmp": ("int", ["const char *", "const char *"], False),
    "strncmp": ("int", ["const char *", "const char *", "size_t"], False),
    "strcasecmp": ("int", ["const char *", "const char *"], False),
    "strncasecmp": ("int", ["const char *", "const char *", "size_t"], False),
    "strchr": ("char *", ["const char *", "int"], False),
    "strrchr": ("char *", ["const char *", "int"], False),
    "strstr": ("char *", ["const char *", "const char *"], False),
    "strdup": ("char *", ["const char *"], False),
    "strtok": ("char *", ["char *", "const char *"], False),
    "strspn": ("size_t", ["const char *", "const char *"], False),
    "strcspn": ("size_t", ["const char *", "const char *"], False),
    "strerror": ("char *", ["int"], False),
    "__strcpy_chk": ("char *", ["char *", "const char *", "size_t"], False),
    "__strcat_chk": ("char *", ["char *", "const char *", "size_t"], False),
    "__strncpy_chk": ("char *", ["char *", "const char *", "size_t", "size_t"], False),
    "memcpy": ("void *", ["void *", "const void *", "size_t"], False),
    "memmove": ("void *", ["void *", "const void *", "size_t"], False),
    "memset": ("void *", ["void *", "int", "size_t"], False),
    "memcmp": ("int", ["const void *", "const void *", "size_t"], False),
    "memchr": ("void *", ["const void *", "int", "size_t"], False),
    "__memcpy_chk": ("void *", ["void *", "const void *", "size_t", "size_t"], False),
    "__memmove_chk": ("void *", ["void *", "const void *", "size_t", "size_t"], False),
    "__memset_chk": ("void *", ["void *", "int", "size_t", "size_t"], False),
    "bzero": ("void", ["void *", "size_t"], False),
    # stdlib
    "malloc": ("void *", ["size_t"], False),
    "calloc": ("void *", ["size_t", "size_t"], False),
    "realloc": ("void *", ["void *", "size_t"], False),
    "free": ("void", ["void *"], False),
    "atoi": ("int", ["const char *"], False),
    "atol": ("long", ["const char *"], False),
    "atof": ("double", ["const char *"], False),
    "strtol": ("long", ["const char *", "char **", "int"], False),
    "strtoul": ("unsigned long", ["const char *", "char **", "int"], False),
    "strtoll": ("long long", ["const char *", "char **", "int"], False),
    "abs": ("int", ["int"], False),
    "labs": ("long", ["long"], False),
    "rand": ("int", [], False),
    "srand": ("void", ["unsigned int"], False),
    "qsort": ("void", ["void *", "size_t", "size_t", "void *"], False),
    "bsearch": ("void *", ["const void *", "const void *", "size_t", "size_t", "void *"], False),
    "getenv": ("char *", ["const char *"], False),
    "system": ("int", ["const char *"], False),
    "atexit": ("int", ["void *"], False),
    # ctype
    "isalpha": ("int", ["int"], False), "isdigit": ("int", ["int"], False), "isalnum": ("int", ["int"], False),
    "isspace": ("int", ["int"], False), "isupper": ("int", ["int"], False), "islower": ("int", ["int"], False),
    "toupper": ("int", ["int"], False), "tolower": ("int", ["int"], False), "isprint": ("int", ["int"], False),
    "isxdigit": ("int", ["int"], False), "ispunct": ("int", ["int"], False),
    # unistd / posix
    "read": ("ssize_t", ["int", "void *", "size_t"], False),
    "write": ("ssize_t", ["int", "const void *", "size_t"], False),
    "open": ("int", ["const char *", "int"], True),
    "close": ("int", ["int"], False),
    "lseek": ("long", ["int", "long", "int"], False),
    "unlink": ("int", ["const char *"], False),
    "sleep": ("unsigned int", ["unsigned int"], False),
    "usleep": ("int", ["unsigned int"], False),
    "getpid": ("int", [], False),
    "fork": ("int", [], False),
    "execve": ("int", ["const char *", "char **", "char **"], False),
    "execl": ("int", ["const char *", "const char *"], True),
    "execv": ("int", ["const char *", "char **"], False),
    "execvp": ("int", ["const char *", "char **"], False),
    "dup2": ("int", ["int", "int"], False),
    "pipe": ("int", ["int *"], False),
    "wait": ("int", ["int *"], False),
    "waitpid": ("int", ["int", "int *", "int"], False),
    "kill": ("int", ["int", "int"], False),
    "signal": ("void *", ["int", "void *"], False),
    "time": ("long", ["long *"], False),
    "ptrace": ("long", ["int", "int", "void *", "void *"], False),
    "mmap": ("void *", ["void *", "size_t", "int", "int", "int", "long"], False),
    "munmap": ("int", ["void *", "size_t"], False),
    "mprotect": ("int", ["void *", "size_t", "int"], False),
    "socket": ("int", ["int", "int", "int"], False),
    "connect": ("int", ["int", "void *", "unsigned int"], False),
    "bind": ("int", ["int", "void *", "unsigned int"], False),
    "listen": ("int", ["int", "int"], False),
    "accept": ("int", ["int", "void *", "unsigned int *"], False),
    "send": ("ssize_t", ["int", "const void *", "size_t", "int"], False),
    "recv": ("ssize_t", ["int", "void *", "size_t", "int"], False),
    "htons": ("unsigned short", ["unsigned short"], False),
    "htonl": ("unsigned int", ["unsigned int"], False),
    "ntohs": ("unsigned short", ["unsigned short"], False),
    "ntohl": ("unsigned int", ["unsigned int"], False),
    "pthread_create": ("int", ["void *", "void *", "void *", "void *"], False),
    "pthread_join": ("int", ["unsigned long", "void **"], False),
    "pthread_mutex_lock": ("int", ["void *"], False),
    "pthread_mutex_unlock": ("int", ["void *"], False),
    "__libc_start_main": ("int", ["void *", "int", "char **", "void *", "void *", "void *", "void *"], False),
    "__cxa_atexit": ("int", ["void *", "void *", "void *"], False),
    # Windows
    "GetProcAddress": ("void *", ["void *", "const char *"], False),
    "LoadLibraryA": ("void *", ["const char *"], False),
    "GetModuleHandleA": ("void *", ["const char *"], False),
    "MessageBoxA": ("int", ["void *", "const char *", "const char *", "unsigned int"], False),
    "VirtualAlloc": ("void *", ["void *", "size_t", "unsigned int", "unsigned int"], False),
    "VirtualProtect": ("int", ["void *", "size_t", "unsigned int", "unsigned int *"], False),
    "CreateFileA": ("void *", ["const char *", "unsigned int", "unsigned int", "void *", "unsigned int", "unsigned int", "void *"], False),
    "ReadFile": ("int", ["void *", "void *", "unsigned int", "unsigned int *", "void *"], False),
    "WriteFile": ("int", ["void *", "const void *", "unsigned int", "unsigned int *", "void *"], False),
    "CloseHandle": ("int", ["void *"], False),
    "GetSystemTimeAsFileTime": ("void", ["void *"], False),
    "GetCurrentThreadId": ("unsigned int", [], False),
    "GetCurrentProcessId": ("unsigned int", [], False),
    "GetCurrentProcess": ("void *", [], False),
    "GetCurrentThread": ("void *", [], False),
    "GetTickCount": ("unsigned int", [], False),
    "GetTickCount64": ("unsigned long long", [], False),
    "QueryPerformanceCounter": ("int", ["void *"], False),
    "QueryPerformanceFrequency": ("int", ["void *"], False),
    "GetStartupInfoW": ("void", ["void *"], False),
    "GetStartupInfoA": ("void", ["void *"], False),
    "GetModuleHandleW": ("void *", ["const wchar_t *"], False),
    "GetModuleHandleA": ("void *", ["const char *"], False),
    "GetModuleFileNameW": ("unsigned int", ["void *", "wchar_t *", "unsigned int"], False),
    "GetModuleFileNameA": ("unsigned int", ["void *", "char *", "unsigned int"], False),
    "GetProcAddress": ("void *", ["void *", "const char *"], False),
    "LoadLibraryW": ("void *", ["const wchar_t *"], False),
    "LoadLibraryA": ("void *", ["const char *"], False),
    "LoadLibraryExW": ("void *", ["const wchar_t *", "void *", "unsigned int"], False),
    "FreeLibrary": ("int", ["void *"], False),
    "GetCommandLineW": ("wchar_t *", [], False),
    "GetCommandLineA": ("char *", [], False),
    "SetLastError": ("void", ["unsigned int"], False),
    "CreateFileW": ("void *", ["const wchar_t *", "unsigned int", "unsigned int", "void *", "unsigned int", "unsigned int", "void *"], False),
    "CreateFileA": ("void *", ["const char *", "unsigned int", "unsigned int", "void *", "unsigned int", "unsigned int", "void *"], False),
    "ReadFile": ("int", ["void *", "void *", "unsigned int", "unsigned int *", "void *"], False),
    "VirtualAlloc": ("void *", ["void *", "size_t", "unsigned int", "unsigned int"], False),
    "VirtualFree": ("int", ["void *", "size_t", "unsigned int"], False),
    "VirtualProtect": ("int", ["void *", "size_t", "unsigned int", "unsigned int *"], False),
    "HeapAlloc": ("void *", ["void *", "unsigned int", "size_t"], False),
    "HeapFree": ("int", ["void *", "unsigned int", "void *"], False),
    "GetProcessHeap": ("void *", [], False),
    "HeapCreate": ("void *", ["unsigned int", "size_t", "size_t"], False),
    "WaitForSingleObject": ("unsigned int", ["void *", "unsigned int"], False),
    "CreateThread": ("void *", ["void *", "size_t", "void *", "void *", "unsigned int", "unsigned int *"], False),
    "MessageBoxA": ("int", ["void *", "const char *", "const char *", "unsigned int"], False),
    "MessageBoxW": ("int", ["void *", "const wchar_t *", "const wchar_t *", "unsigned int"], False),
    "GetStdHandle": ("void *", ["unsigned int"], False),
    "SetConsoleMode": ("int", ["void *", "unsigned int"], False),
    "WriteConsoleW": ("int", ["void *", "const wchar_t *", "unsigned int", "unsigned int *", "void *"], False),
    "TerminateProcess": ("int", ["void *", "unsigned int"], False),
    "InitializeSListHead": ("void", ["void *"], False),
    "SetUnhandledExceptionFilter": ("void *", ["void *"], False),
    "UnhandledExceptionFilter": ("int", ["void *"], False),
    "IsProcessorFeaturePresent": ("int", ["unsigned int"], False),
    "GetEnvironmentStringsW": ("wchar_t *", [], False),
    "FreeEnvironmentStringsW": ("int", ["wchar_t *"], False),
    "EnterCriticalSection": ("void", ["void *"], False),
    "LeaveCriticalSection": ("void", ["void *"], False),
    "InitializeCriticalSection": ("void", ["void *"], False),
    "DeleteCriticalSection": ("void", ["void *"], False),
    "TlsGetValue": ("void *", ["unsigned int"], False),
    "TlsSetValue": ("int", ["unsigned int", "void *"], False),
    "TlsAlloc": ("unsigned int", [], False),
    "lstrlenW": ("int", ["const wchar_t *"], False),
    "lstrlenA": ("int", ["const char *"], False),
    "wcslen": ("size_t", ["const wchar_t *"], False),
    "wcscpy": ("wchar_t *", ["wchar_t *", "const wchar_t *"], False),
    "wcscmp": ("int", ["const wchar_t *", "const wchar_t *"], False),
    "wcscat": ("wchar_t *", ["wchar_t *", "const wchar_t *"], False),
    "IsDebuggerPresent": ("int", [], False),
    "GetLastError": ("unsigned int", [], False),
    "Sleep": ("void", ["unsigned int"], False),
    "ExitProcess": ("void", ["unsigned int"], False),
}


def proto(name: str):
    """이름 → (반환형, 인자형 목록, 가변인자) 또는 None. 앞쪽 밑줄·'@plt'·'.' 접미사를 벗겨 찾는다."""
    if not name:
        return None
    n = name.split("@")[0]
    p = _P.get(n)
    if p is None and n.startswith("_") and n.lstrip("_") in _P and n not in ("__libc_start_main", "__cxa_atexit"):
        p = _P.get(n.lstrip("_"))
    return p


_INT4 = {"int", "unsigned int", "unsigned", "uint32_t", "int32_t", "DWORD", "BOOL", "UINT", "LONG", "ULONG", "float"}
_INT12 = {"char", "unsigned char", "short", "unsigned short", "bool", "uint8_t", "uint16_t", "BYTE", "WORD"}


def ret_width(rtype: str):
    """반환형 문자열 → rax 에서 의미 있는 바이트 수(void 면 0, 모르면 None)."""
    if not rtype:
        return None
    if rtype == "void":
        return 0
    if rtype in _INT4:
        return 4
    if rtype in _INT12:
        return 4
    if rtype.endswith("*") or rtype in ("size_t", "ssize_t", "long", "unsigned long", "off_t", "time_t", "intptr_t", "uintptr_t", "HANDLE",
                                           "long long", "unsigned long long", "int64_t", "uint64_t", "ptrdiff_t", "off64_t"):
        return 8
    return None


FP_TYPES = {"double": 8, "float": 4}


def fp_kinds(types) -> list:
    """원형의 인자형 목록 → 부동소수점 인자들의 폭(8 double · 4 float)을 순서대로."""
    return [FP_TYPES[t] for t in types if t in FP_TYPES]


def printf_kinds(fmt: str) -> list:
    """printf 계열 형식 문자열이 소비하는 인자의 종류: 'i' 정수·포인터, 'd' 실수(double, %Lf 는 x87 이라 'x')."""
    out = []
    i, L = 0, len(fmt)
    while i < L:
        if fmt[i] != "%":
            i += 1
            continue
        i += 1
        if i < L and fmt[i] == "%":
            i += 1
            continue
        while i < L and fmt[i] in "-+ #0'":
            i += 1
        if i < L and fmt[i] == "*":
            out.append("i")
            i += 1
        else:
            while i < L and fmt[i].isdigit():
                i += 1
        if i < L and fmt[i] == ".":
            i += 1
            if i < L and fmt[i] == "*":
                out.append("i")
                i += 1
            else:
                while i < L and fmt[i].isdigit():
                    i += 1
        mods = ""
        while i < L and fmt[i] in "hlLqjzt":
            mods += fmt[i]
            i += 1
        if i < L and fmt[i] in "diouxXeEfFgGaAcspnC S":
            out.append(("x" if "L" in mods else "d") if fmt[i] in "eEfFgGaA" else "i")
            i += 1
    return out


def is_noreturn(name: str) -> bool:
    if not name:
        return False
    n = name.split("@")[0]
    return n in NORETURN or n.lstrip("_") in {"exit", "abort", "assert_fail"}


def printf_specs(fmt: str) -> int:
    """printf 계열 형식 문자열이 소비하는 인자 수(`*` 폭·정밀도 포함). %% 는 세지 않는다."""
    n = 0
    i = 0
    L = len(fmt)
    while i < L:
        if fmt[i] != "%":
            i += 1
            continue
        i += 1
        if i < L and fmt[i] == "%":
            i += 1
            continue
        while i < L and fmt[i] in "-+ #0'":
            i += 1
        if i < L and fmt[i] == "*":
            n += 1
            i += 1
        else:
            while i < L and fmt[i].isdigit():
                i += 1
        if i < L and fmt[i] == ".":
            i += 1
            if i < L and fmt[i] == "*":
                n += 1
                i += 1
            else:
                while i < L and fmt[i].isdigit():
                    i += 1
        while i < L and fmt[i] in "hlLqjzt":
            i += 1
        if i < L and fmt[i] in "diouxXeEfFgGaAcspnC S":
            n += 1
            i += 1
    return n
