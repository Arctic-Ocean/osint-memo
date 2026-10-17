#include <stdio.h>
#include <string.h>
static int add(int a, int b) { return a + b; }
int check(const char *pw) {
    const char *secret = "letmein";
    if (strcmp(pw, secret) == 0) return 1;
    return 0;
}
int main(int argc, char **argv) {
    int s = add(2, 3);
    printf("sum=%d\n", s);
    if (argc > 1 && check(argv[1])) puts("access granted");
    else puts("denied");
    return 0;
}
