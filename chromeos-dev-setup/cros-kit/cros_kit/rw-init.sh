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
  # RW_CONTRACT=validated: the exact validated OS. RW_CONTRACT=trial: a newer
  # release whose original startup job is unchanged; rw_trial decides with
  # stateful health markers whether saved changes may follow the update.
  RW_CONTRACT=
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
    os_version=$(sed -n 's/^CHROMEOS_RELEASE_VERSION=//p' /etc/lsb-release)
    printf '%s\n' "$os_version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || exit 1
    kernel=$(uname -r)
    case "$kernel" in ''|[!a-zA-Z0-9]*|*[!a-zA-Z0-9._+-]*) exit 1 ;; esac
    actual_root=
    set -f
    for argument in $(cat /proc/cmdline); do
      case "$argument" in root_hexdigest=*)
        [ -z "$actual_root" ] || exit 1
        actual_root=${argument#*=} ;;
      esac
    done
    case "$actual_root" in ''|*[!0-9a-f]*) exit 1 ;; esac
    [ "${#actual_root}" = 64 ] || exit 1
    # Both modes require the original startup job that rw_wrap_startup edits.
    [ "$(sha256sum /etc/init/startup.conf | awk '{ print $1 }')" = "$FLEX_STARTUP_SHA256" ] || exit 1
    if [ "$os_version" = "$FLEX_OS_VERSION" ]; then
      [ "$kernel" = "$FLEX_KERNEL_RELEASE" ] && [ "$actual_root" = "$FLEX_ROOT_SHA256" ] || exit 1
      exit 0
    fi
    # Only releases newer than the validated one may be tried; never downgrades.
    awk -v current="$os_version" -v validated="$FLEX_OS_VERSION" 'BEGIN {
      split(current, c, "."); split(validated, v, ".")
      for (i = 1; i <= 3; i++) {
        if (c[i] + 0 > v[i] + 0) exit 0
        if (c[i] + 0 < v[i] + 0) exit 1
      }
      exit 1
    }' || exit 1
    exit 10
  )
  case "$?" in
    0) RW_CONTRACT=validated ;;
    10) RW_CONTRACT=trial ;;
    *)
      rw_warn 'unsupported or invalid compatibility contract; saved OS changes will not be applied.'
      return 1 ;;
  esac
}

rw_trial() {
  # Decide whether saved changes may follow a newer, unvalidated OS. The first
  # boot of each verified root is a trial; the health job (see rw_compat) marks
  # it good once the login screen appears. A trial that never reached the login
  # screen is rejected on the next boot, which then stays stock.
  rw_directory "$RW_STORE" || return 1
  digest=$(sed -n 1p "$RW_RUNTIME/os-root") || return 1
  case "$digest" in ''|*[!0-9a-f]*) return 1 ;; esac
  [ "${#digest}" = 64 ] || return 1
  policy="$RW_STORE/unvalidated-os"
  rejected="$RW_STORE/os-rejected"
  healthy="$RW_STORE/os-healthy"
  trial="$RW_STORE/os-trial"
  for path in "$policy" "$rejected" "$healthy" "$trial"; do
    [ ! -L "$path" ] || return 1
    [ ! -e "$path" ] || [ -f "$path" ] || return 1
  done
  if [ -e "$policy" ] && [ "$(cat "$policy")" != trial ]; then
    rw_warn 'unvalidated OS: saved OS changes disabled by unvalidated-os setting.'
    return 1
  fi
  if [ -f "$rejected" ] && grep -qxF "$digest" "$rejected"; then
    rw_warn 'unvalidated OS was rejected after a failed trial; saved OS changes will not be applied.'
    return 1
  fi
  if [ -f "$healthy" ] && grep -qxF "$digest" "$healthy"; then
    rw_warn 'unvalidated OS passed its trial boot; applying saved OS changes.'
    return 0
  fi
  if [ -f "$trial" ] && [ "$(cat "$trial")" = "$digest" ]; then
    printf '%s\n' "$digest" >> "$rejected" && sync -f "$rejected" || return 1
    rm -f "$trial"
    rw_warn 'unvalidated OS trial did not reach the login screen; rejected, saved OS changes will not be applied.'
    return 1
  fi
  printf '%s\n' "$digest" > "$trial.next" && mv -T "$trial.next" "$trial" &&
    sync -f "$trial" || return 1
  rw_warn 'unvalidated newer OS: trial boot with saved OS changes.'
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
  mkdir -p "$compat/usr/lib64/flex-debug" "$compat/usr/share/minijail" "$compat/etc/init" || return 1
  cp -aL /usr/lib64/librootdev.so.1 \
    "$compat/usr/lib64/flex-debug/librootdev-stock.so.1" || return 1
  cp -p "$RW_RUNTIME/rootdev-compat.so" "$compat$RW_LIBRARY" || return 1
  stat -c %d / > "$compat/usr/lib64/flex-debug/root-device" || return 1
  chmod 644 "$compat/usr/lib64/flex-debug/root-device" || return 1
  for directory in / /usr /usr/lib64 /usr/share /usr/share/minijail /etc /etc/init; do
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
  # Health marker for unvalidated-OS trials (rw_trial): only a trial boot that
  # reaches the login screen records its verified root as healthy.
  health="$compat/etc/init/cros-kit-health.conf"
  cat > "$health" <<'EOF' || return 1
description "CrOS Kit: mark a trial OS healthy once the login screen appears"
start on login-prompt-visible
task
script
  digest=$(sed -n 1p /tmp/flex-debug/os-root) || exit 0
  store=/mnt/stateful_partition/flex-debug
  [ -f "$store/os-trial" ] && [ "$(cat "$store/os-trial")" = "$digest" ] || exit 0
  grep -qxF "$digest" "$store/os-healthy" 2>/dev/null ||
    printf '%s\n' "$digest" >> "$store/os-healthy"
  rm -f "$store/os-trial"
  sync
end script
EOF
  chmod 644 "$health" || return 1
  if [ -e /sys/fs/selinux/enforce ]; then
    chcon --reference=/etc/init/startup.conf "$health" || return 1
  fi
  sync -f "$compat" && touch "$layer/compat.ready" && sync -f "$layer/compat.ready"
}

rw_edit_path() {
  # Derived edits own regular files under /etc only; no traversal or oddities.
  printf '%s\n' "$1" | grep -Eq '^/etc(/[A-Za-z0-9_][A-Za-z0-9._+-]*)+$' || return 1
  case "$1" in */../*|*/./*|*/..|*/.) return 1 ;; esac
}

rw_derive() {
  # Saved line edits are data, recomputed from the current verified file on
  # every boot so they follow OS updates instead of freezing an old copy:
  #   drop-line PATH TEXT    remove lines equal to TEXT
  #   append-line PATH TEXT  append TEXT
  # Results are written to the upper tree before the overlay is mounted.
  rules="$RW_STORE/edits.conf"
  applied="$RW_STORE/edits.applied"
  paths="$RW_RUNTIME/edit-paths"
  for path in "$rules" "$applied"; do
    [ ! -L "$path" ] || return 1
    [ ! -e "$path" ] || [ -f "$path" ] || return 1
  done
  : > "$paths" || return 1
  if [ -f "$rules" ]; then
    bytes=$(wc -c < "$rules") || return 1
    [ "$bytes" -eq "$(LC_ALL=C tr -d '\000' < "$rules" | wc -c)" ] || return 1
    while IFS= read -r entry || [ -n "$entry" ]; do
      case "$entry" in ''|\#*) continue ;; esac
      operation=${entry%% *}
      rest=${entry#* }
      path=${rest%% *}
      [ "$rest" != "$entry" ] && [ "${rest#* }" != "$rest" ] || return 1
      case "$operation" in drop-line|append-line) ;; *) return 1 ;; esac
      rw_edit_path "$path" || return 1
      printf '%s\n' "$path" >> "$paths" || return 1
    done < "$rules"
  fi
  LC_ALL=C sort -u "$paths" > "$paths.sorted" || return 1
  # Edits removed from the rules give their path back to the verified OS.
  if [ -f "$applied" ]; then
    while IFS= read -r path || [ -n "$path" ]; do
      rw_edit_path "$path" || return 1
      grep -qxF "$path" "$paths.sorted" || rm -f "$layer/upper$path" || return 1
    done < "$applied"
  fi
  while IFS= read -r path; do
    source="$RW_RUNTIME/lower$path"
    target="$layer/upper$path"
    if [ ! -f "$source" ] || [ -L "$source" ]; then
      rw_warn "edit target $path is not a regular file in this OS; edit skipped."
      rm -f "$target" || return 1
      continue
    fi
    content="$RW_RUNTIME/edit-content"
    command cp "$source" "$content" || return 1
    while IFS= read -r entry || [ -n "$entry" ]; do
      case "$entry" in ''|\#*) continue ;; esac
      operation=${entry%% *}
      rest=${entry#* }
      [ "${rest%% *}" = "$path" ] || continue
      text=${rest#* }
      case "$operation" in
        drop-line)
          { grep -vxF -- "$text" "$content" || [ "$?" = 1 ]; } > "$content.next" || return 1
          mv -f "$content.next" "$content" || return 1 ;;
        append-line)
          printf '%s\n' "$text" >> "$content" || return 1 ;;
      esac
    done < "$rules"
    # Mirror missing parent directories with the verified modes and labels.
    parent=
    for component in $(printf '%s\n' "${path%/*}" | tr '/' ' '); do
      parent="$parent/$component"
      [ ! -L "$layer/upper$parent" ] || return 1
      if [ ! -d "$layer/upper$parent" ]; then
        mkdir "$layer/upper$parent" || return 1
        chmod "$(stat -c %a "$RW_RUNTIME/lower$parent")" "$layer/upper$parent" || return 1
        if [ -e /sys/fs/selinux/enforce ]; then
          chcon --reference="$RW_RUNTIME/lower$parent" "$layer/upper$parent" || return 1
        fi
      fi
    done
    [ ! -L "$target" ] || return 1
    command cp -p "$source" "$target.cros-kit" || return 1
    cat "$content" > "$target.cros-kit" || return 1
    if [ -e /sys/fs/selinux/enforce ]; then
      chcon --reference="$source" "$target.cros-kit" || return 1
    fi
    mv -f "$target.cros-kit" "$target" || return 1
  done < "$paths.sorted"
  command cp "$paths.sorted" "$applied.next" && mv -f "$applied.next" "$applied" &&
    sync -f "$applied"
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
  if [ "$RW_CONTRACT" = trial ]; then
    rw_trial || return 1
  fi
  rw_layer && rw_mount_list && rw_compat || return 1
  newroot="$RW_RUNTIME/newroot"
  mkdir "$newroot" "$RW_RUNTIME/lower" || return 1
  mount --bind / "$RW_RUNTIME/lower" || return 1
  rw_derive || return 1
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
