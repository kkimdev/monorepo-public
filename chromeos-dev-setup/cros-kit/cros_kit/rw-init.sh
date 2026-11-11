#!/bin/sh
# Persistent host settings over the unchanged verified root.

PATH=/sbin:/bin:/usr/sbin:/usr/bin
export PATH
RW_RUNTIME=/tmp/flex-debug
RW_STORE=/mnt/stateful_partition/flex-debug
RW_STOCK=/run/flex-debug/stock
RW_MOVED=
RW_PIVOTED=0

rw_warn() {
  printf 'Flex Debug: %s\n' "$*" >&2
}

rw_supported() {
  # Parse data, never shell code. An unknown OS must not import saved changes.
  (
    bytes=$(wc -c < "$RW_RUNTIME/compatibility.conf") || exit 1
    # dash read silently drops NUL bytes; reject them even in comments or keys.
    [ "$bytes" -eq "$(LC_ALL=C tr -d '\000' < "$RW_RUNTIME/compatibility.conf" | wc -c)" ] || exit 1
    FLEX_OS_VERSION= FLEX_ROOT_SHA256= FLEX_KERNEL_RELEASE= FLEX_STARTUP_SHA256=
    while IFS= read -r entry || [ -n "$entry" ]; do
      case "$entry" in ''|\#*) continue ;; esac
      value=${entry#*=}
      case "$entry" in
        FLEX_OS_VERSION=*)
          [ -z "$FLEX_OS_VERSION" ] || exit 1
          printf '%s\n' "$value" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || exit 1
          FLEX_OS_VERSION=$value ;;
        FLEX_ROOT_SHA256=*)
          [ -z "$FLEX_ROOT_SHA256" ] || exit 1
          case "$value" in ''|*[!0-9a-f]*) exit 1 ;; esac
          [ "${#value}" = 64 ] || exit 1
          FLEX_ROOT_SHA256=$value ;;
        FLEX_KERNEL_RELEASE=*)
          [ -z "$FLEX_KERNEL_RELEASE" ] || exit 1
          case "$value" in ''|[!a-zA-Z0-9]*|*[!a-zA-Z0-9._+-]*) exit 1 ;; esac
          FLEX_KERNEL_RELEASE=$value ;;
        FLEX_STARTUP_SHA256=*)
          [ -z "$FLEX_STARTUP_SHA256" ] || exit 1
          case "$value" in ''|*[!0-9a-f]*) exit 1 ;; esac
          [ "${#value}" = 64 ] || exit 1
          FLEX_STARTUP_SHA256=$value ;;
        *) exit 1 ;;
      esac
    done < "$RW_RUNTIME/compatibility.conf"
    [ -n "$FLEX_OS_VERSION" ] && [ -n "$FLEX_ROOT_SHA256" ] &&
      [ -n "$FLEX_KERNEL_RELEASE" ] && [ -n "$FLEX_STARTUP_SHA256" ] || exit 1
    [ "$(sed -n 's/^CHROMEOS_RELEASE_VERSION=//p' /etc/lsb-release)" = "$FLEX_OS_VERSION" ] || exit 1
    [ "$(uname -r)" = "$FLEX_KERNEL_RELEASE" ] || exit 1
    actual_root=
    set -f
    for argument in $(cat /proc/cmdline); do
      case "$argument" in root_hexdigest=*)
        [ -z "$actual_root" ] || exit 1
        actual_root=${argument#*=} ;;
      esac
    done
    [ "$actual_root" = "$FLEX_ROOT_SHA256" ] || exit 1
    [ "$(sha256sum /etc/init/startup.conf | awk '{ print $1 }')" = "$FLEX_STARTUP_SHA256" ]
  ) || {
    rw_warn 'unsupported or invalid compatibility contract; saved OS changes will not be applied.'
    return 1
  }
}

rw_directory() {
  [ ! -L "$1" ] || return 1
  [ -d "$1" ] || mkdir -m 700 "$1" || return 1
  [ "$(stat -c %u "$1")" = 0 ] || return 1
  chmod 700 "$1"
}

rw_layer() {
  version=$(cat "$RW_RUNTIME/version") || return 1
  case "$version" in ''|*[!0-9a-f]*) return 1 ;; esac
  [ "${#version}" = 64 ] || return 1
  rw_directory "$RW_STORE" || return 1
  layer="$RW_STORE/root"
  # Recover the sole interruption window between the two activation renames.
  if [ ! -e "$layer" ] && [ ! -L "$layer" ] &&
      [ -d "$RW_STORE/root.previous" ]; then
    rw_directory "$RW_STORE/root.previous" &&
      mv -T "$RW_STORE/root.previous" "$layer" || return 1
  fi
  fresh=0
  [ -e "$layer" ] || [ -L "$layer" ] || fresh=1
  rw_directory "$layer" || return 1
  rw_directory "$layer/upper" && rw_directory "$layer/work" || return 1
  if [ "$fresh" = 0 ] && [ "$(cat "$layer/version" 2>/dev/null)" != "$version" ]; then
    # xino is needed by Minijail. Never reuse its old lower inode references
    # after an OS replacement: import saved changes into a fresh upper tree.
    next="$RW_STORE/root.next"
    rw_directory "$next" || return 1
    rm -rf -- "$next" || return 1
    mkdir -m 700 "$next" "$next/upper" "$next/work" || return 1
    mkfifo -m 600 "$RW_RUNTIME/import" || return 1
    # Include raw ACL xattrs as well; ChromeOS tar has no libacl support.
    tar --create --sparse --file=- --directory="$layer/upper" --selinux \
      --xattrs --xattrs-include='*' --xattrs-exclude='trusted.overlay.origin' \
      --xattrs-exclude='trusted.overlay.impure' --xattrs-exclude='trusted.overlay.nlink' \
      --xattrs-exclude='trusted.overlay.upper' --xattrs-exclude='trusted.overlay.uuid' \
      . > "$RW_RUNTIME/import" &
    producer=$!
    imported=0
    tar --extract --file=- --directory="$next/upper" --same-owner \
      --selinux --xattrs --xattrs-include='*' < "$RW_RUNTIME/import" || imported=1
    wait "$producer" || imported=1
    rm "$RW_RUNTIME/import"
    [ "$imported" = 0 ] || return 1
    printf '%s\n' "$version" > "$next/version" || return 1
    sync -f "$next/version" || return 1
    if [ -e "$RW_STORE/root.previous" ] || [ -L "$RW_STORE/root.previous" ]; then
      rw_directory "$RW_STORE/root.previous" &&
        rm -rf -- "$RW_STORE/root.previous" || return 1
    fi
    mv -T "$layer" "$RW_STORE/root.previous" || return 1
    mv -T "$next" "$layer" || return 1
    sync -f "$RW_STORE" || return 1
  fi
  chmod "$(stat -c %a /)" "$layer/upper" || return 1
  if [ -e /sys/fs/selinux/enforce ]; then
    chcon --reference=/ "$layer/upper" || return 1
  fi
  # Durable, atomic marker: a lost/missing marker forces a fresh import.
  printf '%s\n' "$version" > "$layer/version.next" &&
    mv -T "$layer/version.next" "$layer/version" && sync -f "$layer/version"
}

rw_mount_list() {
  # Snapshot before adding our own mounts; moving a parent preserves its whole
  # subtree, mount flags and propagation. Reject encoded mountpoint names.
  awk '$5 != "/" {
         if ($5 ~ /\\/) exit 1
         print $5
       }' /proc/self/mountinfo > "$RW_RUNTIME/mounts-all" || return 1
  LC_ALL=C sort -u "$RW_RUNTIME/mounts-all" > "$RW_RUNTIME/mounts-sorted" || return 1
  awk '{ for (parent in seen) if (index($0, parent "/") == 1) next
         seen[$0] = 1; print }' "$RW_RUNTIME/mounts-sorted" > "$RW_RUNTIME/mounts-top" || return 1
  # Keep mount helpers' /proc available immediately. The runtime and staged
  # new root live under /tmp; move that subtree last, after pivot.
  awk 'BEGIN { print "/proc" } $0 != "/tmp" && $0 != "/proc" { print }
       END { print "/tmp" }' \
    "$RW_RUNTIME/mounts-top" > "$RW_RUNTIME/mounts"
}

rw_compat() {
  # A tool-owned lower layer contains compatibility, never user settings.
  # Actual files (rather than file bind mounts) survive Minijail's nonrecursive
  # root bind. Keep its inodes stable until the next fresh upper import.
  compat="$layer/compat"
  if [ -f "$layer/compat.ready" ]; then
    [ -d "$compat" ] && [ ! -L "$compat" ]
    return
  fi
  rw_directory "$compat" && rm -rf -- "$compat" || return 1
  RW_LIBRARY=$(readlink -f /usr/lib64/librootdev.so.1) || return 1
  [ "${RW_LIBRARY%/*}" = /usr/lib64 ] || return 1
  mkdir -p "$compat/usr/lib64/flex-debug" "$compat/usr/share/minijail" || return 1
  cp -aL /usr/lib64/librootdev.so.1 \
    "$compat/usr/lib64/flex-debug/librootdev-stock.so.1" || return 1
  cp -p "$RW_RUNTIME/rootdev-compat.so" "$compat$RW_LIBRARY" || return 1
  stat -c %d / > "$compat/usr/lib64/flex-debug/root-device" || return 1
  chmod 644 "$compat/usr/lib64/flex-debug/root-device" || return 1
  for directory in / /usr /usr/lib64 /usr/share /usr/share/minijail; do
    chmod "$(stat -c %a "$directory")" "$compat$directory" || return 1
    if [ -e /sys/fs/selinux/enforce ]; then
      chcon --reference="$directory" "$compat$directory" || return 1
    fi
  done
  chmod 755 "$compat/usr/lib64/flex-debug" || return 1
  if [ -e /sys/fs/selinux/enforce ]; then
    chcon --reference=/usr/lib64 "$compat/usr/lib64/flex-debug" \
      "$compat/usr/lib64/flex-debug/root-device" || return 1
    chcon --reference="$RW_LIBRARY" "$compat$RW_LIBRARY" || return 1
  fi
  # Preserve LoadPin's verified submounts in jails that clone their root
  # nonrecursively. Never make the whole bind recursive: that exposes data.
  for policy in /usr/share/minijail/*.conf; do
    [ -f "$policy" ] || continue
    if awk '
      /^[[:space:]]*bind-mount[[:space:]]*=[[:space:]]*\/(,|[[:space:]]*$)/ { found=1 }
      /^[[:space:]]*profile[[:space:]]*=[[:space:]]*minimalistic-mountns/ { found=1 }
      END { exit !found }
    ' "$policy"; then
      cp -a "$policy" "$compat$policy" || return 1
      printf '\n# Keep verified kernel files when binding the OS root.\nbind-mount = /lib/modules\nbind-mount = /lib/firmware\n' \
        >> "$compat$policy" || return 1
    fi
  done
  sync -f "$compat" && touch "$layer/compat.ready" && sync -f "$layer/compat.ready"
}

rw_rollback() {
  if [ "$RW_PIVOTED" = 1 ]; then
    if mountpoint -q "$RW_STOCK"; then
      mkdir -p /.flex-debug-stock || return 1
      mount --move "$RW_STOCK" /.flex-debug-stock || return 1
    fi
    for destination in $RW_MOVED; do
      mount --move "$destination" "/.flex-debug-stock$destination" || return 1
    done
    cd / || return 1
    pivot_root /.flex-debug-stock "/.flex-debug-stock$RW_RUNTIME/newroot" || return 1
    cd / || return 1
    RW_PIVOTED=0
  fi
  for directory in /lib/modules /lib/firmware; do
    mountpoint -q "$RW_RUNTIME/newroot$directory" &&
      umount "$RW_RUNTIME/newroot$directory"
  done
  if mountpoint -q "$RW_RUNTIME/newroot"; then
    umount "$RW_RUNTIME/newroot" || return 1
  fi
  if mountpoint -q "$RW_RUNTIME/lower"; then
    umount "$RW_RUNTIME/lower" || return 1
  fi
}

rw_apply() {
  # Native startup handles stateful/encryption/wipe first, on the stock root.
  umount /etc/init || return 1
  rw_supported || return 1
  mountpoint -q /mnt/stateful_partition || return 1
  rw_layer && rw_mount_list && rw_compat || return 1
  newroot="$RW_RUNTIME/newroot"
  mkdir "$newroot" "$RW_RUNTIME/lower" || return 1
  mount --bind / "$RW_RUNTIME/lower" || return 1
  mount -t overlay overlay -o "lowerdir=$layer/compat:$RW_RUNTIME/lower,upperdir=$layer/upper,workdir=$layer/work,index=off,metacopy=off,xino=on,redirect_dir=nofollow,nfs_export=off" "$newroot" || return 1
  # LoadPin accepts kernel modules and firmware only from the verified root.
  # These are trust boundaries, independent of any personal configuration fix.
  for directory in /lib/modules /lib/firmware; do
    [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
    [ -d "$newroot$directory" ] && [ ! -L "$newroot$directory" ] || return 1
    mount --bind "$RW_RUNTIME/lower$directory" "$newroot$directory" || return 1
  done
  # Validate every mount destination before changing the process roots.
  while read -r destination; do
    [ "$(readlink -f "$newroot$destination")" = "$newroot$destination" ] || return 1
    if [ -d "$destination" ]; then
      [ -d "$newroot$destination" ] || return 1
    else
      [ -f "$newroot$destination" ] || return 1
    fi
  done < "$RW_RUNTIME/mounts"
  mkdir -p "$newroot/.flex-debug-stock" || return 1
  [ ! -L "$newroot/.flex-debug-stock" ] || return 1
  cd / || return 1
  # pivot_root updates PID1 and all other processes rooted at the old /.
  pivot_root "$newroot" "$newroot/.flex-debug-stock" || return 1
  RW_PIVOTED=1
  cd / || return 1
  while read -r destination; do
    mount --move "/.flex-debug-stock$destination" "$destination" || return 1
    RW_MOVED="$destination $RW_MOVED"
  done < "/.flex-debug-stock$RW_RUNTIME/mounts"
  mkdir -p /run/flex-debug/stock || return 1
  chmod 755 /run/flex-debug /run/flex-debug/stock || return 1
  mount --move /.flex-debug-stock "$RW_STOCK" || return 1
  rmdir /.flex-debug-stock || return 1
  # The compatibility lower delegates disk lookup to this OS's own library.
  rootdev -s || return 1
  [ "$(stat -c %d /)" = "$(stat -c %d /usr/share/minijail)" ] || return 1
  initctl reload-configuration || return 1
  rw_warn 'persistent OS root enabled; data mounts and verified modules remain direct.'
}

rw_recovery() {
  message='FATAL: system mounts could not be restored. Reboot and select the original ChromeOS Flex boot entry.'
  # stderr still holds the boot log open even if /tmp has already moved.
  rw_warn "$message" || :
  if [ -c /dev/console ]; then
    printf 'Flex Debug: %s\n' "$message" > /dev/console || :
  fi
  # Keep startup's main script alive: ordinary services require stopped startup.
  # Automatic reboot would retry the same failure without selecting stock.
  while :; do sleep 60 || :; done
}

rw_apply_or_continue() {
  if ! rw_apply; then
    rw_warn 'writable overlay failed; restoring stock mounts.'
    if ! rw_rollback; then
      rw_recovery
      return "$?"
    fi
    initctl reload-configuration || rw_warn 'could not reload stock startup jobs.'
  fi
  # Continue ordinary startup only with the overlay or restored stock mounts.
  return 0
}

rw_wrap_startup() {
  awk '
    /^exec[[:space:]]/ {
      count++
      print "script"
      sub(/^exec[[:space:]]+/, "  if "); print
      print "  then"
      print "    /bin/sh /tmp/flex-debug/rw-init.sh --apply >> /tmp/flex-debug/boot.log 2>&1"
      print "  else"
      print "    exit \"$?\""
      print "  fi"
      print "end script"
      next
    }
    { print }
    END { if (count != 1) exit 1 }
  ' "$1" > "$2"
}

rw_prepare() {
  for filesystem in proc sysfs devtmpfs; do
    options=nosuid,nodev,noexec
    case "$filesystem" in
      proc) destination=/proc ;;
      sysfs) destination=/sys ;;
      *) destination=/dev; options=nosuid,noexec ;;
    esac
    mountpoint -q "$destination" || mount -t "$filesystem" -o "$options" "$filesystem" "$destination" || return 1
  done
  # Upstart reuses this mount, so apply its stock restrictions here.
  mountpoint -q /tmp || mount -t tmpfs -o nosuid,nodev,noexec,nosymfollow,mode=1777 tmpfs /tmp || return 1
  umask 077
  mkdir -m 700 "$RW_RUNTIME" || return 1
  # ChromeOS shells reject scripts on noexec mounts, even via /bin/sh.
  # Keep /tmp restricted and make only this root-private runtime executable.
  mount -t tmpfs -o nosuid,nodev,exec,mode=0700 tmpfs "$RW_RUNTIME" || return 1
  exec </dev/null >>"$RW_RUNTIME/boot.log" 2>&1
  cp "${FLEX_DEBUG_INIT_SOURCE:-$0}" "$RW_RUNTIME/rw-init.sh" || return 1
  cp "$(dirname "${FLEX_DEBUG_INIT_SOURCE:-$0}")/compatibility.conf" \
    "$RW_RUNTIME/compatibility.conf" || return 1
  rw_supported || return 1
  cp "$(dirname "${FLEX_DEBUG_INIT_SOURCE:-$0}")/rootdev-compat.so" \
    "$RW_RUNTIME/rootdev-compat.so" || return 1
  chmod 755 "$RW_RUNTIME/rootdev-compat.so" || return 1
  # Lower identity includes the verified OS, compatibility code and root device.
  # A tool update also rebuilds lower metadata before reusing saved changes.
  sed -n 's/.*root_hexdigest=\([0-9a-f]\{64\}\).*/\1/p' /proc/cmdline > "$RW_RUNTIME/os-root"
  [ "$(wc -c < "$RW_RUNTIME/os-root")" = 65 ] || return 1
  stat -c %d / >> "$RW_RUNTIME/os-root" || return 1
  sha256sum "$RW_RUNTIME/os-root" "$RW_RUNTIME/rw-init.sh" \
    "$RW_RUNTIME/rootdev-compat.so" "$RW_RUNTIME/compatibility.conf" \
    > "$RW_RUNTIME/inputs" || return 1
  sha256sum "$RW_RUNTIME/inputs" | awk '{ print $1 }' > "$RW_RUNTIME/version"
  [ -s "$RW_RUNTIME/version" ] || return 1
  cp -a /etc/init "$RW_RUNTIME/init" || return 1
  # Keep the original command and its failure status. Only an in-memory copy
  # changes; nothing is written to the signed filesystem.
  rw_wrap_startup /etc/init/startup.conf "$RW_RUNTIME/init/startup.conf" || return 1
  mount --bind "$RW_RUNTIME/init" /etc/init
}

rw_main() {
  if [ "${1:-}" = --apply ]; then
    rw_apply_or_continue
    return
  fi
  if ! rw_prepare; then
    rw_warn 'bootstrap failed; continuing with stock startup.'
  fi
  # The loader mounted this read-only solely to read the bootstrap script.
  # dash still has its source file open; exec below closes that descriptor.
  if mountpoint -q /tmp/flex-debug-esp; then
    umount -l /tmp/flex-debug-esp || rw_warn 'could not detach Debug ESP.'
  fi
  umask 022
  exec /sbin/init
}

rw_main "$@"
