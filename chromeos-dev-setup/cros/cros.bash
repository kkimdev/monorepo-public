# cros: apply, update, and publish chromeos-dev-setup. See ../DESIGN.md.
# Packaged with writeShellApplication (errexit, nounset, pipefail, shellcheck).

readonly HOST_DIR="${CROS_HOST_DIR:-$HOME/.config/cros/host}"
readonly HOST_JSON="$HOST_DIR/host.json"
readonly SETUP_SUBDIR="chromeos-dev-setup"
readonly REMINDER_DAYS=14
readonly HM_PROFILE="$HOME/.local/state/nix/profiles/home-manager"
readonly NIX_FLAGS=(--accept-flake-config)
# Desktop session units; `cros reset --hard` restarts these, earlyoom keeps running.
readonly SESSION_UNITS=(atuin-daemon.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service)

log() { printf 'cros: %s\n' "$*" >&2; }
die() {
  log "error: $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage: cros <command> [options]

  sync              Pull main, upgrade Debian and Nix, then apply.
  apply             Apply the current checkout (system package + Home Manager).
  check             Build everything without installing or activating.
  bump [input...]   Maintainer: update flake inputs, apply, commit flake.lock.
  publish           Maintainer: push committed updates to origin/main.
  clean             Collect Nix garbage and optimise the store.
  reset [--hard]    Start inactive desktop services; --hard restarts them
                    (running GUI apps close).

apply options: --checkout DIR (first install), --maintainer.
EOF
}

# ---------------------------------------------------------------- checkout

recorded_checkout() {
  [[ -f "$HOST_JSON" ]] || return 0
  jq -r '.checkout // empty' "$HOST_JSON"
}

# Prints the repository root for an explicit directory or the recorded one.
resolve_checkout() {
  local requested="${1:-}" root
  if [[ -z "$requested" ]]; then
    requested="$(recorded_checkout)"
  fi
  [[ -n "$requested" ]] ||
    die "no checkout is recorded in $HOST_JSON; run: bash <checkout>/$SETUP_SUBDIR/setup.bash"
  root="$(git -C "$requested" rev-parse --show-toplevel 2>/dev/null)" ||
    die "not a Git checkout: $requested"
  [[ -f "$root/$SETUP_SUBDIR/flake.nix" ]] || die "missing $root/$SETUP_SUBDIR/flake.nix"
  printf '%s\n' "$root"
}

# A directory inside a Git checkout is a git+file flake reference.
flake_dir() { printf '%s/%s' "$1" "$SETUP_SUBDIR"; }

# Sparse checkouts must contain every repository path the flake reads: the
# setup directory plus the relative path inputs recorded in flake.lock.
ensure_sparse_paths() {
  local root="$1" setup paths
  [[ "$(git -C "$root" config --bool core.sparseCheckout 2>/dev/null || true)" == true ]] || return 0
  setup="$(flake_dir "$root")"
  mapfile -t paths < <(
    jq -r '.nodes[].locked | select(.type? == "path") | .path' "$setup/flake.lock" |
      while IFS= read -r path; do realpath -m --relative-to="$root" "$setup/$path"; done |
      grep -v -e "^$SETUP_SUBDIR\(/\|$\)" | sort -u
  )
  git -C "$root" sparse-checkout add "$SETUP_SUBDIR" "${paths[@]}"
}

require_clean_main() {
  local root="$1" branch
  branch="$(git -C "$root" symbolic-ref --quiet --short HEAD || true)"
  [[ "$branch" == main ]] || die "this command requires the main branch; current: ${branch:-detached HEAD}"
  [[ -z "$(git -C "$root" status --porcelain --untracked-files=all)" ]] ||
    die "this command requires a clean checkout, including untracked files"
}

# ---------------------------------------------------------------- host data

# Writes host.json atomically. $2 is "keep", "true", or "false" for maintainer.
write_host_data() {
  local root="$1" maintainer="$2" gtk3 gtk4 tmp
  gtk3="$(find /usr/lib -type f -path '*/gtk-3.0/*/immodules.cache' -print -quit 2>/dev/null || true)"
  gtk4="$(find /usr/lib -type d -path '*/gtk-4.0' -print -quit 2>/dev/null || true)"
  if [[ -z "$gtk3" ]] || ! grep -q 'im-cros-gtk3\.so' "$gtk3"; then
    die "GTK3's immodules cache is missing or does not list cros-im; reinstall libgtk-3-0 and cros-im"
  fi
  [[ -n "$gtk4" ]] || die "GTK4's module directory was not found under /usr/lib; reinstall cros-im"
  if [[ "$maintainer" == keep ]]; then
    maintainer=false
    if [[ -f "$HOST_JSON" ]]; then
      maintainer="$(jq -r '.maintainer // false' "$HOST_JSON")"
    fi
  fi
  mkdir -p "$HOST_DIR"
  tmp="$(mktemp "$HOST_DIR/.host.json.XXXXXX")"
  jq -n \
    --arg username "$USER" \
    --arg homeDirectory "$HOME" \
    --arg checkout "$root" \
    --arg gtk3Cache "$gtk3" \
    --arg gtk4Path "$gtk4" \
    --argjson maintainer "$maintainer" \
    '{$username, $homeDirectory, $checkout, $gtk3Cache, $gtk4Path, $maintainer}' >"$tmp"
  mv -f -- "$tmp" "$HOST_JSON"
}

is_maintainer() {
  [[ -f "$HOST_JSON" ]] && [[ "$(jq -r '.maintainer // false' "$HOST_JSON")" == true ]]
}

# ---------------------------------------------------------------- builds

install_system_package() {
  local root="$1" out deb want have
  out="$(nix build "${NIX_FLAGS[@]}" --no-link --print-out-paths "$(flake_dir "$root")#system-deb")" ||
    die "building the system package failed"
  deb="$out/cros-dev-system.deb"
  want="$(dpkg-deb --field "$deb" Version)"
  # Status matters too: a failed postinst leaves the same version half-configured.
  # shellcheck disable=SC2016 # dpkg-query fields, not shell variables.
  have="$(dpkg-query --showformat='${db:Status-Abbrev}${Version}' --show cros-dev-system 2>/dev/null || true)"
  if [[ "$have" == "ii $want" ]]; then
    log "system package $want is current"
    return 0
  fi
  log "installing system package $want (installed: ${have:-none})"
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades "$deb"
}

# Prints the activation package path for the current user.
build_home() {
  local root="$1"
  [[ -f "$HOST_JSON" ]] || die "missing $HOST_JSON; run 'cros apply' first"
  nix build "${NIX_FLAGS[@]}" --no-link --print-out-paths \
    --override-input host "path:$HOST_DIR" \
    "$(flake_dir "$root")#homeConfigurations.\"$USER\".activationPackage"
}

# ---------------------------------------------------------------- migrations
# One-time moves from the pre-2026-11 setup. Each step is idempotent. Delete
# this block (and its calls in cmd_apply) once every machine has run
# `cros apply`; see "Migrations run by cros apply" in DESIGN.md.

migrate_git_identity() {
  local legacy="$HOME/.config/home-manager/host.json" key value
  if [[ ! -f "$legacy" ]]; then
    legacy="$HOME/.config/home-manager.legacy/host.json"
  fi
  [[ -f "$legacy" ]] || return 0
  for key in name email; do
    # Only ~/.gitconfig counts: the old Home Manager config still holds identity.
    if git config --file "$HOME/.gitconfig" --get "user.$key" >/dev/null 2>&1; then
      continue
    fi
    value="$(jq -r --arg key "git${key^}" '.[$key] // empty' "$legacy")"
    if [[ -n "$value" ]]; then
      git config --file "$HOME/.gitconfig" "user.$key" "$value"
      log "moved Git user.$key to ~/.gitconfig"
    fi
  done
}

migrate_legacy_config_dir() {
  local old="$HOME/.config/home-manager" new="$HOME/.config/home-manager.legacy"
  [[ -f "$old/host.json" ]] || return 0
  if [[ -e "$new" ]]; then
    log "warning: both $old and $new exist; leaving them unchanged"
    return 0
  fi
  mv -- "$old" "$new"
  log "moved the old generated configuration to $new"
}

# The old activation copied the profile's entries and icons; remove exact
# copies so Home Manager can link entries and unrelated files stay untouched.
migrate_desktop_copies() {
  local share="$HOME/.local/share" profile="$HM_PROFILE/home-path/share" kind file rel
  for kind in applications icons; do
    [[ -d "$share/$kind" ]] || continue
    while IFS= read -r -d '' file; do
      rel="${file#"$share/$kind/"}"
      if cmp -s -- "$file" "$profile/$kind/$rel" ||
        { [[ "$kind" == applications ]] && cmp -s -- "$file" "/usr/share/applications/$rel"; }; then
        rm -f -- "$file"
      fi
    done < <(find "$share/$kind" -type f -print0)
    find "$share/$kind" -mindepth 1 -depth -type d -empty -delete
  done
}

migrate_before_activation() {
  migrate_git_identity
  migrate_legacy_config_dir
  migrate_desktop_copies
}

# Runs after activation, when ~/.bashrc already starts zsh.
migrate_after_activation() {
  local current
  current="$(getent passwd "$USER" | cut -d: -f7)"
  if [[ "$current" == "$HOME/.nix-profile/bin/zsh" || "$current" == /nix/store/* ]]; then
    log "switching the login shell to /bin/bash; ~/.bashrc starts zsh"
    sudo chsh -s /bin/bash "$USER"
  fi
}

# ---------------------------------------------------------------- reminders

print_reminders() {
  local root="$1" ahead last days
  [[ -z "${CROS_NO_REMINDERS:-}" ]] || return 0
  is_maintainer || return 0
  [[ "$(git -C "$root" symbolic-ref --quiet --short HEAD || true)" == main ]] || return 0
  ahead="$(git -C "$root" rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"
  if ((ahead > 0)); then
    log "reminder: $ahead commit(s) are not published; run 'cros publish' when ready"
  fi
  # A shallow clone lacks the history that dates the last lock change.
  [[ "$(git -C "$root" rev-parse --is-shallow-repository)" == false ]] || return 0
  last="$(git -C "$root" log -1 --format=%ct -- "$SETUP_SUBDIR/flake.lock" 2>/dev/null || true)"
  if [[ -n "$last" ]]; then
    days=$((($(date +%s) - last) / 86400))
    if ((days >= REMINDER_DAYS)); then
      log "reminder: flake inputs were last updated $days days ago; run 'cros bump'"
    fi
  fi
}

# ---------------------------------------------------------------- commands

cmd_apply() {
  local checkout="" maintainer=keep root activation
  while (($#)); do
    case "$1" in
      --checkout)
        (($# >= 2)) || die "--checkout needs a directory"
        checkout="$2"
        shift 2
        ;;
      --maintainer)
        maintainer=true
        shift
        ;;
      *) die "unknown apply option: $1" ;;
    esac
  done
  root="$(resolve_checkout "$checkout")"
  ensure_sparse_paths "$root"
  install_system_package "$root"
  write_host_data "$root" "$maintainer"
  migrate_before_activation
  activation="$(build_home "$root")" || die "the Home Manager build failed; nothing was activated"
  [[ -x "$activation/activate" ]] || die "no activation script in $activation"
  HOME_MANAGER_BACKUP_EXT=backup "$activation/activate" ||
    die "Home Manager activation failed; inspect its output, or run the previous generation's activate"
  migrate_after_activation
  start_desktop_services
  log "applied $root"
  log "running services pick up changes at next login or after 'cros reset --hard'"
  print_reminders "$root"
}

cmd_check() {
  local root
  (($# == 0)) || die "check takes no arguments"
  root="$(resolve_checkout "")"
  ensure_sparse_paths "$root"
  nix build "${NIX_FLAGS[@]}" --no-link --print-out-paths "$(flake_dir "$root")#system-deb"
  build_home "$root"
  log "build passed; nothing was installed or activated"
}

cmd_sync() {
  local root
  (($# == 0)) || die "sync takes no arguments"
  root="$(resolve_checkout "")"
  require_clean_main "$root"
  git -C "$root" pull --ff-only origin main
  ensure_sparse_paths "$root"
  sudo apt-get update
  sudo env DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y
  sudo env DEBIAN_FRONTEND=noninteractive apt-get autoremove -y
  if command -v determinate-nixd >/dev/null 2>&1; then
    sudo determinate-nixd upgrade ||
      log "warning: the Determinate Nix upgrade failed; continuing with the installed Nix"
  fi
  # Apply with the code that was just pulled.
  exec nix run "${NIX_FLAGS[@]}" "$(flake_dir "$root")#cros" -- apply
}

print_input_changes() {
  python3 -I - "$1" "$2" <<'PY'
import json
import sys
import time

old, new = (json.load(open(path))["nodes"] for path in sys.argv[1:])

def describe(node):
    locked = node.get("locked", {})
    revision = (locked.get("rev") or locked.get("narHash") or locked.get("path") or "?")[:12]
    modified = locked.get("lastModified")
    date = time.strftime("%Y-%m-%d", time.gmtime(modified)) if modified else "-"
    return f"{revision} ({date})"

for name in sorted(set(old) | set(new)):
    if name == "root" or old.get(name, {}).get("locked") == new.get(name, {}).get("locked"):
        continue
    before = describe(old[name]) if name in old else "added"
    after = describe(new[name]) if name in new else "removed"
    print(f"- {name}: {before} -> {after}")
PY
}

print_package_changes() {
  local before="$1" after="$2"
  [[ -n "$before" && -n "$after" && "$before" != "$after" ]] || return 0
  nix store diff-closures "$before" "$after" 2>/dev/null |
    sed 's/\x1b\[[0-9;]*m//g' | grep -F '→' |
    sed 's/^ *//; s/, [-+]\?[0-9.]* [KMGT]\?i\?B$//; s/^/- /' || true
}

print_workarounds() {
  local file="$1/$SETUP_SUBDIR/workarounds.json" id type repo number remove_when state
  [[ -f "$file" ]] || return 0
  echo "Workarounds:"
  while IFS=$'\t' read -r id type repo number remove_when; do
    state="check manually"
    if [[ "$type" == github-pr || "$type" == github-issue ]] && command -v gh >/dev/null 2>&1; then
      state="$(gh "${type#github-}" view "$number" --repo "$repo" --json state --jq .state 2>/dev/null || echo unknown)"
    fi
    echo "- $id: $state; remove when $remove_when"
  done < <(jq -r '.[] | [.id, .check.type, (.check.repo // "-"), (.check.number // "-" | tostring), .removeWhen] | @tsv' "$file")
}

# Until bump commits, any exit (failure, Ctrl-C) puts the previous lock back.
BUMP_LOCK=""
BUMP_PREVIOUS=""
BUMP_COMMITTED=0
finish_bump() {
  [[ -n "$BUMP_PREVIOUS" ]] || return 0
  if ((!BUMP_COMMITTED)) && ! cmp -s -- "$BUMP_PREVIOUS" "$BUMP_LOCK"; then
    cp -- "$BUMP_PREVIOUS" "$BUMP_LOCK"
    log "restored the previous flake.lock; run 'cros apply' if the new one was activated"
  fi
  rm -f -- "$BUMP_PREVIOUS"
}

cmd_bump() {
  local root lock previous before after inputs packages workarounds message
  root="$(resolve_checkout "")"
  require_clean_main "$root"
  lock="$root/$SETUP_SUBDIR/flake.lock"
  previous="$(mktemp)"
  BUMP_LOCK="$lock"
  BUMP_PREVIOUS="$previous"
  trap finish_bump EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  cp -- "$lock" "$previous"
  before="$(readlink -f "$HM_PROFILE" 2>/dev/null || true)"

  nix flake update "${NIX_FLAGS[@]}" "$@" --flake "$(flake_dir "$root")"
  if cmp -s -- "$previous" "$lock"; then
    log "flake inputs are already current"
    print_reminders "$root"
    return 0
  fi
  CROS_NO_REMINDERS=1 nix run "${NIX_FLAGS[@]}" "$(flake_dir "$root")#cros" -- apply ||
    die "apply failed with the updated inputs"
  after="$(readlink -f "$HM_PROFILE" 2>/dev/null || true)"

  inputs="$(print_input_changes "$previous" "$lock")"
  packages="$(print_package_changes "$before" "$after")"
  workarounds="$(print_workarounds "$root")"
  message="chore(chromeos-dev-setup): update flake inputs"$'\n\nInputs:\n'"$inputs"
  if [[ -n "$packages" ]]; then
    message+=$'\n\nPackages:\n'"$packages"
  fi
  printf '%s\n' "$message"
  [[ -z "$workarounds" ]] || printf '\n%s\n' "$workarounds"
  git -C "$root" commit --quiet -m "$message" -- "$SETUP_SUBDIR/flake.lock"
  BUMP_COMMITTED=1
  log "committed $(git -C "$root" rev-parse --short HEAD); run 'cros publish' to release it"
}

cmd_publish() {
  local root ahead
  (($# == 0)) || die "publish takes no arguments"
  root="$(resolve_checkout "")"
  require_clean_main "$root"
  git -C "$root" fetch origin main
  git -C "$root" merge-base --is-ancestor origin/main HEAD ||
    die "origin/main has commits that are not in this checkout; run 'cros sync' first"
  ahead="$(git -C "$root" rev-list --count origin/main..HEAD)"
  if ((ahead == 0)); then
    log "nothing to publish"
    return 0
  fi
  git -C "$root" log --oneline origin/main..HEAD
  git -C "$root" push origin HEAD:main
  log "published $ahead commit(s)"
}

cmd_clean() {
  (($# == 0)) || die "clean takes no arguments"
  nix-collect-garbage -d
  nix store optimise
}

cmd_reset() {
  local hard=0
  case "${1:-}" in
    "") ;;
    --hard) hard=1 ;;
    *) die "unknown reset option: $1" ;;
  esac
  if ((hard)); then
    log "restarting desktop services; running GUI apps will close"
    systemctl --user stop "${SESSION_UNITS[@]}"
    rm -f "/run/user/$UID/wayland-1.lock" "/run/user/$UID/wayland-2.lock" \
      "/run/user/$UID/wayland-1" "/run/user/$UID/wayland-2"
  fi
  start_desktop_services
}

# Starting an inactive unit never disturbs running apps.
start_desktop_services() {
  log "starting inactive desktop services"
  systemctl --user daemon-reload
  systemctl --user reset-failed earlyoom.service "${SESSION_UNITS[@]}"
  systemctl --user start earlyoom.service "${SESSION_UNITS[@]}"
}

main() {
  local command="${1:-}"
  (($#)) && shift
  case "$command" in
    sync) cmd_sync "$@" ;;
    apply) cmd_apply "$@" ;;
    check) cmd_check "$@" ;;
    bump) cmd_bump "$@" ;;
    publish) cmd_publish "$@" ;;
    clean) cmd_clean "$@" ;;
    reset) cmd_reset "$@" ;;
    -h | --help | help) usage ;;
    "")
      usage
      exit 2
      ;;
    *)
      usage >&2
      die "unknown command: $command"
      ;;
  esac
}

main "$@"
