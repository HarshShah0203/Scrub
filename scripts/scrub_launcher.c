/*
 * Tiny Mach-O binary used as CFBundleExecutable for Scrub.app.
 *
 * macOS Launch Services (Spotlight, Dock, open -a) often refuses bundles
 * whose main executable is a shell script (error -54, or "(null)" in UI).
 * This stub execs the real bash launcher next to it: Scrub-inner.sh
 */
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(void)
{
    char exe[PATH_MAX];
    uint32_t sz = (uint32_t)sizeof(exe);
    if (_NSGetExecutablePath(exe, &sz) != 0)
        return 1;

    char *slash = strrchr(exe, '/');
    if (!slash || slash == exe)
        return 1;
    *slash = '\0';

    char script[PATH_MAX];
    int n = snprintf(script, sizeof(script), "%s/Scrub-inner.sh", exe);
    if (n < 0 || (size_t)n >= sizeof(script))
        return 1;

    char *const argv[] = {"/bin/bash", script, NULL};
    execv("/bin/bash", argv);
    perror("execv");
    return 1;
}
