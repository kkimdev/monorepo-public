"""Exercise device discovery without mounting or changing the host root."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


class RootdevCompatTests(unittest.TestCase):
    def test_overlay_and_stock_queries(self):
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or not shutil.which(compiler[0]):
            self.skipTest("C compiler unavailable (run in nix develop)")
        source = Path(__file__).with_name("rootdev-compat.c").read_text()
        # Inject only system-call and symbol-resolution probes, leaving the
        # implementation's control flow and ABI intact.
        source = source.replace("dlsym(", "probe_dlsym(")
        source = source.replace("statfs(", "probe_statfs(")
        source = source.replace("stat(", "probe_stat(")
        source = source.replace("open(", "probe_open(")
        source = source.replace("read(", "probe_read(")
        source = source.replace("close(", "probe_close(")
        harness = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/vfs.h>
#include <unistd.h>
static void *probe_dlsym(void *, const char *);
static int probe_statfs(const char *, struct statfs *);
static int probe_stat(const char *, struct stat *);
static int probe_open(const char *, int, ...);
static ssize_t probe_read(int, void *, size_t);
static int probe_close(int);
'''
        harness += source
        harness += r'''
static int overlay = 1, device_missing, symbol_missing, interrupted, read_error;
static const char *device_contents = "123\n";
static size_t position;
static int wrapper_calls, root_calls;
static const char *seen_mount, *seen_search, *seen_dev_path;
static dev_t seen_dev;
static bool seen_full, seen_strip;
static int probe_statfs(const char *path, struct statfs *out) {
  assert(!strcmp(path, "/"));
  out->f_type = overlay ? OVERLAYFS_MAGIC : 0xef53;
  return 0;
}
static int probe_stat(const char *path, struct stat *out) {
  if (!strcmp(path, "/missing")) { errno = ENOENT; return -1; }
  if (!strcmp(path, "/other")) { out->st_dev = 789; return 0; }
  assert(!strcmp(path, "/") || !strcmp(path, "/.") ||
         !strcmp(path, "/usr") || !strcmp(path, "./"));
  out->st_dev = 456;
  return 0;
}
static int probe_open(const char *path, int flags, ...) {
  assert(!strcmp(path, FLEX_ROOT_DEVICE));
  assert(flags == (O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
  position = 0;
  if (device_missing) { errno = ENOENT; return -1; }
  return 42;
}
static ssize_t probe_read(int fd, void *buffer, size_t size) {
  assert(fd == 42);
  if (interrupted) { interrupted = 0; errno = EINTR; return -1; }
  if (read_error) { errno = EIO; return -1; }
  size_t count = strlen(device_contents) - position;
  /* Force short reads to exercise the parser's accumulation loop. */
  if (count > 2) count = 2;
  if (count > size) count = size;
  memcpy(buffer, device_contents + position, count);
  position += count;
  return (ssize_t)count;
}
static int probe_close(int fd) { assert(fd == 42); return 0; }
static int original_wrapper(char *path, size_t size, bool full, bool strip,
    dev_t *dev, const char *mount, const char *search, const char *dev_path) {
  assert(path && size == 20);
  wrapper_calls++;
  seen_mount = mount; seen_dev = dev ? *dev : 0;
  seen_search = search; seen_dev_path = dev_path;
  seen_full = full; seen_strip = strip;
  if (dev) *dev = 999;
  return 77;
}
static int original_root(char *path, size_t size, bool full, bool strip) {
  assert(path && size == 20 && full && strip);
  root_calls++;
  return 88;
}
static void *probe_dlsym(void *scope, const char *symbol) {
  assert(scope == RTLD_NEXT);
  if (symbol_missing) return NULL;
  if (!strcmp(symbol, "rootdev_wrapper")) return original_wrapper;
  assert(!strcmp(symbol, "rootdev"));
  return original_root;
}
int main(void) {
  char path[20];
  dev_t dev = 456;
  assert(rootdev_wrapper(path, 20, true, false, &dev, "/", "search", "dev") == 77);
  assert(wrapper_calls == 1 && seen_dev == 123 && dev == 999);
  assert(!strcmp(seen_mount, "/"));
  assert(!strcmp(seen_search, "search") && !strcmp(seen_dev_path, "dev"));
  assert(seen_full && !seen_strip);
  assert(rootdev(path, 20, false, true) == 77);
  assert(wrapper_calls == 2 && seen_dev == 123 && !seen_full && seen_strip);
  assert(!seen_search && !seen_dev_path);
  /* The stock CLI accepts arbitrary paths, including root aliases. */
  const char *root_paths[] = { "/usr", "/.", "./" };
  for (size_t i = 0; i < sizeof(root_paths) / sizeof(root_paths[0]); ++i) {
    dev = 456;
    assert(rootdev_wrapper(path, 20, true, false, &dev, root_paths[i], NULL, NULL) == 77);
    assert(seen_dev == 123 && !strcmp(seen_mount, root_paths[i]));
  }
  /* Explicit devices and paths on separate mounts must not be rewritten. */
  dev = 321;
  assert(rootdev_wrapper(path, 20, false, false, &dev, "/", NULL, NULL) == 77);
  assert(seen_dev == 321 && !strcmp(seen_mount, "/"));
  dev = 321;
  assert(rootdev_wrapper(path, 20, false, false, &dev, "/usr", NULL, NULL) == 77);
  assert(seen_dev == 321 && !strcmp(seen_mount, "/usr"));
  dev = 789;
  assert(rootdev_wrapper(path, 20, false, false, &dev, "/other", NULL, NULL) == 77);
  assert(seen_dev == 789 && !strcmp(seen_mount, "/other"));
  dev = 456;
  assert(rootdev_wrapper(path, 20, false, false, &dev, "/other", NULL, NULL) == 77);
  assert(seen_dev == 456 && !strcmp(seen_mount, "/other"));
  dev = 456;
  assert(rootdev_wrapper(path, 20, false, false, &dev, "/missing", NULL, NULL) == 77);
  assert(seen_dev == 456 && !strcmp(seen_mount, "/missing"));
  assert(rootdev_wrapper(path, 20, false, false, NULL, "/", NULL, NULL) == 77);
  assert(seen_dev == 0 && !strcmp(seen_mount, "/"));
  overlay = 0;
  assert(rootdev(path, 20, true, true) == 88 && root_calls == 1);
  dev = 456;
  assert(rootdev_wrapper(path, 20, false, false, &dev, "/", NULL, NULL) == 77);
  assert(seen_dev == 456 && !strcmp(seen_mount, "/"));
  overlay = 1; device_missing = 1;
  assert(rootdev(path, 20, false, false) == -1 && errno == ENOENT);
  device_missing = 0; read_error = 1;
  assert(rootdev(path, 20, false, false) == -1 && errno == EIO);
  read_error = 0; interrupted = 1;
  assert(rootdev(path, 20, false, false) == 77 && seen_dev == 123);
  const char *invalid[] = { "", "\n", "abc", "123x", "123\n4", "-1", " 123",
    "18446744073709551616", "11111111111111111111111111111111111" };
  for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
    device_contents = invalid[i];
    assert(rootdev(path, 20, false, false) == -1 && errno == EINVAL);
  }
  device_contents = "18446744073709551615\n";
  assert(rootdev(path, 20, false, false) == 77 && seen_dev == (dev_t)-1);
  device_contents = "123";
  assert(rootdev(path, 20, false, false) == 77 && seen_dev == 123);
  symbol_missing = 1;
  assert(rootdev(path, 20, false, false) == -1 && errno == ENOSYS);
  overlay = 0;
  assert(rootdev(path, 20, true, true) == -1 && errno == ENOSYS);
  return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="rootdev-compat-") as temporary:
            path = Path(temporary)
            (path / "test.c").write_text(harness)
            compiled = subprocess.run(
                compiler + ["-Wall", "-Wextra", "-Werror", "-std=gnu11",
                            str(path / "test.c"), "-o", str(path / "test")],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr or compiled.stdout)
            subprocess.run([str(path / "test")], check=True)


if __name__ == "__main__":
    unittest.main()
