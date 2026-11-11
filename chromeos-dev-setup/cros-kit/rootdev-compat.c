/* Keep ChromeOS root-device discovery on the verified lower filesystem. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stddef.h>
#include <sys/stat.h>
#include <sys/vfs.h>
#include <unistd.h>

#ifndef FLEX_ROOT_DEVICE
#define FLEX_ROOT_DEVICE "/usr/lib64/flex-debug/root-device"
#endif
#define OVERLAYFS_MAGIC 0x794c7630
#define PUBLIC __attribute__((visibility("default")))

typedef int (*wrapper_fn)(char *, size_t, bool, bool, dev_t *, const char *,
                          const char *, const char *);
typedef int (*rootdev_fn)(char *, size_t, bool, bool);

/* This small immutable file lives in the root tree, so nonrecursive root
 * binds in minijails preserve it. No /run mount or additional namespace is
 * needed to find the original block-device number. */
static int original_device(dev_t *dev) {
  char buffer[32];
  size_t length = 0;
  int fd = open(FLEX_ROOT_DEVICE, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0)
    return -1;
  while (length < sizeof(buffer)) {
    ssize_t count = read(fd, buffer + length, sizeof(buffer) - length);
    if (count < 0) {
      if (errno == EINTR)
        continue;
      int saved = errno;
      close(fd);
      errno = saved;
      return -1;
    }
    if (!count)
      break;
    length += (size_t)count;
  }
  close(fd);
  if (!length || length == sizeof(buffer)) {
    errno = EINVAL;
    return -1;
  }
  if (buffer[length - 1] == '\n')
    --length;
  if (!length) {
    errno = EINVAL;
    return -1;
  }
  dev_t value = 0, limit = (dev_t)-1;
  for (size_t i = 0; i < length; ++i) {
    unsigned digit = (unsigned char)buffer[i] - '0';
    if (digit > 9 || value > limit / 10 ||
        (value == limit / 10 && digit > limit % 10)) {
      errno = EINVAL;
      return -1;
    }
    value = value * 10 + digit;
  }
  *dev = value;
  return 0;
}

/* Paths on the root overlay resolve to the verified block device. Explicit
 * device queries and paths on other mounts keep their stock behavior. The
 * original DT_NEEDED library supplies every other public function. */
PUBLIC int rootdev_wrapper(char *path, size_t size, bool full, bool strip,
                           dev_t *dev, const char *mount_path,
                           const char *search, const char *dev_path) {
  wrapper_fn original = (wrapper_fn)dlsym(RTLD_NEXT, "rootdev_wrapper");
  struct statfs fs;
  struct stat root;
  struct stat requested;
  if (!original) {
    errno = ENOSYS;
    return -1;
  }
  if (dev && mount_path &&
      !statfs("/", &fs) && fs.f_type == OVERLAYFS_MAGIC &&
      !stat("/", &root) && *dev == root.st_dev &&
      !stat(mount_path, &requested) && requested.st_dev == root.st_dev) {
    if (original_device(dev))
      return -1;
  }
  return original(path, size, full, strip, dev, mount_path, search, dev_path);
}

PUBLIC int rootdev(char *path, size_t size, bool full, bool strip) {
  struct statfs fs;
  struct stat root;
  if (!statfs("/", &fs) && fs.f_type == OVERLAYFS_MAGIC) {
    if (stat("/", &root))
      return -1;
    dev_t dev = root.st_dev;
    return rootdev_wrapper(path, size, full, strip, &dev, "/", NULL, NULL);
  }
  rootdev_fn original = (rootdev_fn)dlsym(RTLD_NEXT, "rootdev");
  if (!original) {
    errno = ENOSYS;
    return -1;
  }
  return original(path, size, full, strip);
}
