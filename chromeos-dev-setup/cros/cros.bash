# cros: apply, update, and publish chromeos-dev-setup. See ../DESIGN.md.
# Packaged with writeShellApplication (errexit, nounset, pipefail, shellcheck).

readonly HOST_DIR="${CROS_HOST_DIR:-$HOME/.config/cros/host}"
readonly HOST_JSON="$HOST_DIR/host.json"
readonly SETUP_SUBDIR="chromeos-dev-setup"
readonly REMINDER_DAYS=14
# `cros bump` takes third-party inputs only at commits this old, so a
# compromised release has time to be found and pulled before it is installed.
readonly COOLDOWN_DAYS=7
# `cros clean` keeps generations this recent so a bad update can be rolled back.
readonly KEEP_GENERATIONS_DAYS=14
# GTK lives in the x86_64 multiarch directory; i386 GTK may exist beside it.
readonly MULTIARCH_LIB=/usr/lib/x86_64-linux-gnu
# Home Manager's non-NixOS GPU setup links /run/opengl-driver through this file.
readonly GPU_DRIVERS_CONF="${CROS_GPU_DRIVERS_CONF:-/etc/tmpfiles.d/non-nixos-gpu.conf}"
readonly HM_PROFILE="$HOME/.local/state/nix/profiles/home-manager"
readonly NIX_FLAGS=(--accept-flake-config)
# Desktop session units; `cros reset --hard` restarts these, earlyoom keeps running.
readonly SESSION_UNITS=(atuin-daemon.service sommelier@0.service sommelier@1.service sommelier-x@0.service sommelier-x@1.service sommelier-rs.service cros-garcon.service)

log() { printf 'cros: %s\n' "$*" >&2; }
die() {
  log "error: $*"
  exit 1
}

# The command table: "name|arguments|description". `usage`, dispatch in `main`,
# and the zsh completion (via `cros __commands`) all read it.
readonly COMMANDS=(
  "sync||Pull main, upgrade Debian and Nix, then apply."
  "apply|[options]|Apply the current checkout (system package + Home Manager)."
  "check||Build without installing; report whether this machine runs it."
  "audit||Report cooldown shortfalls, downgrades, and pending Debian security fixes."
  "bump|[input...]|Maintainer: pull main, update inputs (cooldown), apply, commit."
  "publish||Maintainer: push bump commits to main, releasing them to all."
  "clean||Delete generations over 14 days old, collect garbage, optimise."
  "reset|[--hard]|Start stopped desktop services; --hard restarts them."
)

usage() {
  local entry name arguments description
  printf 'Usage: cros <command>\n\n'
  for entry in "${COMMANDS[@]}"; do
    IFS='|' read -r name arguments description <<<"$entry"
    printf '  %-17s %s\n' "$name${arguments:+ $arguments}" "$description"
  done
  printf '\napply options: --checkout DIR (first install or testing), --maintainer.\n'
  printf 'bump options: --no-cooldown (take the newest commits; for urgent fixes).\n'
  printf 'Cooldown: third-party inputs move only to commits at least %s days old.\n' "$COOLDOWN_DAYS"
}

# Prints "name:description" per command for the zsh completion.
list_commands() {
  local entry
  for entry in "${COMMANDS[@]}"; do
    printf '%s:%s\n' "${entry%%|*}" "${entry##*|}"
  done
}

is_command() {
  local entry
  for entry in "${COMMANDS[@]}"; do
    [[ "${entry%%|*}" != "$1" ]] || return 0
  done
  return 1
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

# ---------------------------------------------------------------- rollback
# A command that changes a file before work that can fail guards it: any exit
# before keep_file (a failure, Ctrl-C) puts the saved copy back atomically and
# logs the note. One guarded file per run.
GUARDED_FILE=""
GUARDED_COPY=""
GUARDED_NOTE=""

guard_file() {
  local copy
  copy="$(mktemp)"
  cp -- "$1" "$copy"
  GUARDED_FILE="$1"
  GUARDED_COPY="$copy"
  GUARDED_NOTE="$2"
  trap restore_guarded_file EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

keep_file() { GUARDED_FILE=""; }

restore_guarded_file() {
  local tmp
  [[ -n "$GUARDED_COPY" ]] || return 0
  if [[ -n "$GUARDED_FILE" ]] && ! cmp -s -- "$GUARDED_COPY" "$GUARDED_FILE"; then
    tmp="$(mktemp "$(dirname -- "$GUARDED_FILE")/.cros-restore.XXXXXX")"
    cp -- "$GUARDED_COPY" "$tmp"
    mv -f -- "$tmp" "$GUARDED_FILE"
    log "$GUARDED_NOTE"
  fi
  rm -f -- "$GUARDED_COPY"
}

# ---------------------------------------------------------------- host data

# Writes host.json atomically. $2 is "keep", "true", or "false" for maintainer.
write_host_data() {
  local root="$1" maintainer="$2" gtk3 gtk4 tmp
  gtk3="$(find "$MULTIARCH_LIB" -type f -path '*/gtk-3.0/*/immodules.cache' -print -quit 2>/dev/null || true)"
  gtk4="$(find "$MULTIARCH_LIB" -maxdepth 1 -type d -name gtk-4.0 -print -quit 2>/dev/null || true)"
  if [[ -z "$gtk3" ]] || ! grep -q 'im-cros-gtk3\.so' "$gtk3"; then
    die "GTK3's immodules cache is missing or does not list cros-im; reinstall libgtk-3-0 and cros-im"
  fi
  [[ -n "$gtk4" ]] || die "GTK4's module directory was not found under $MULTIARCH_LIB; reinstall cros-im"
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

# Prints the path of the built system package.
build_system_package() {
  local out
  out="$(nix build "${NIX_FLAGS[@]}" --no-link --print-out-paths "$(flake_dir "$1")#system-deb")" ||
    die "building the system package failed"
  printf '%s/cros-dev-system.deb\n' "$out"
}

# Prints the installed package's status and version ("ii 1+abc"), or nothing.
installed_system_package() {
  # shellcheck disable=SC2016 # dpkg-query fields, not shell variables.
  dpkg-query --showformat='${db:Status-Abbrev}${Version}' --show cros-dev-system 2>/dev/null || true
}

# Succeeds when the given package's version is installed and configured.
# Status matters too: a failed postinst leaves the same version half-configured.
system_package_is_installed() {
  [[ "$(installed_system_package)" == "ii $(dpkg-deb --field "$1" Version)" ]]
}

install_system_package() {
  local deb="$1" want have
  want="$(dpkg-deb --field "$deb" Version)"
  if system_package_is_installed "$deb"; then
    log "system package $want is current"
    return 0
  fi
  have="$(installed_system_package)"
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

# Nix programs find GPU drivers through /run/opengl-driver. Home Manager ships
# a setup script per generation but cannot run it, since it needs root; prints
# the generation's script when the installed link is not that generation's.
stale_gpu_setup() {
  local setup
  setup="$(readlink -f "$1/home-path/bin/non-nixos-gpu-setup" 2>/dev/null || true)"
  [[ -x "$setup" ]] || return 0
  [[ "$(readlink -f "$GPU_DRIVERS_CONF" 2>/dev/null || true)" != \
    "$(dirname -- "$(dirname -- "$setup")")/lib/tmpfiles.d/non-nixos-gpu.conf" ]] || return 0
  printf '%s\n' "$setup"
}

sync_gpu_drivers() {
  local setup
  setup="$(stale_gpu_setup "$1")"
  [[ -n "$setup" ]] || return 0
  log "linking this generation's GPU drivers at /run/opengl-driver"
  sudo "$setup"
}

# ---------------------------------------------------------------- reminders

print_reminders() {
  local root="$1" branch primary ahead last days
  [[ -z "${CROS_NO_REMINDERS:-}" ]] || return 0
  branch="$(git -C "$root" symbolic-ref --quiet --short HEAD || true)"
  # Applying a test worktree is temporary; say how to return to main.
  if [[ "$branch" != main ]]; then
    primary="$(git -C "$root" worktree list --porcelain | sed -n '1s/^worktree //p')"
    if [[ -n "$primary" && "$primary" != "$root" ]]; then
      log "reminder: this machine follows $root on ${branch:-detached HEAD}; return with 'cros apply --checkout $primary'"
    else
      log "reminder: this machine follows $root on ${branch:-detached HEAD}; switch it back to main when done"
    fi
    return 0
  fi
  is_maintainer || return 0
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
  local checkout="" maintainer=keep root deb activation
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
  # Build everything before changing the system: a failed build leaves both the
  # system package and the recorded checkout as they were.
  deb="$(build_system_package "$root")"
  # Until activation, a failure keeps this machine on its previous checkout.
  if [[ -f "$HOST_JSON" ]]; then
    guard_file "$HOST_JSON" "restored the previous host data; this machine still follows $(recorded_checkout)"
  fi
  write_host_data "$root" "$maintainer"
  activation="$(build_home "$root")" || die "the Home Manager build failed; nothing was installed or activated"
  [[ -x "$activation/activate" ]] || die "no activation script in $activation"
  install_system_package "$deb"
  HOME_MANAGER_BACKUP_EXT=backup "$activation/activate" ||
    die "Home Manager activation failed; inspect its output, or run the previous generation's activate"
  keep_file
  sync_gpu_drivers "$activation"
  start_desktop_services
  log "applied $root"
  log "running services pick up changes at next login or after 'cros reset --hard'"
  print_reminders "$root"
}

cmd_check() {
  local root deb activation active stale=""
  (($# == 0)) || die "check takes no arguments"
  root="$(resolve_checkout "")"
  ensure_sparse_paths "$root"
  deb="$(build_system_package "$root")"
  activation="$(build_home "$root")" || die "the Home Manager build failed"
  log "build passed; nothing was installed or activated"
  active="$(readlink -f "$HM_PROFILE" 2>/dev/null || true)"
  system_package_is_installed "$deb" || stale+=", system package"
  [[ "$active" == "$activation" ]] || stale+=", Home Manager"
  [[ -z "$(stale_gpu_setup "$activation")" ]] || stale+=", GPU drivers"
  if [[ -z "$stale" ]]; then
    log "this machine runs this build"
  else
    log "this machine does not run this build (${stale#, }); run 'cros apply'"
  fi
}

# ---------------------------------------------------------------- audit
# Answers "did anything this machine runs get worse without our noticing?".
# It reads only local data: git history, store paths, and apt's metadata.
# Outward advisory feeds are deliberately out of scope; see DESIGN.md.

# Prints "<days>.<tenths>" for a duration in seconds; a future date reads 0.
format_days() {
  local seconds="$1"
  ((seconds > 0)) || seconds=0
  printf '%d.%d' $((seconds / 86400)) $(((seconds % 86400) * 10 / 86400))
}

# Prints the store path of the Home Manager generation before the active one.
previous_generation() {
  local current link previous=""
  current="$(readlink -f "$HM_PROFILE" 2>/dev/null || true)"
  [[ -n "$current" ]] || return 0
  # Each link is created in order, so the one before the active link is the
  # previous generation even after a rollback.
  while IFS= read -r link; do
    if [[ "$(readlink -f "$link")" == "$current" ]]; then
      [[ -z "$previous" ]] || printf '%s\n' "$previous"
      return 0
    fi
    previous="$(readlink -f "$link")"
  done < <(find "${HM_PROFILE%/*}" -maxdepth 1 -name "$(basename "$HM_PROFILE")-*-link" | sort -V)
}

# Prints one line per third-party input the lock took with less than
# COOLDOWN_DAYS of soak, measured from the commit that last changed its
# revision. Neither `keep` nor --no-cooldown leaves a trace in flake.lock, so
# history is the only record of whether the gate actually applied.
audit_soak() {
  local root="$1" lock owner name node type repo_owner repo rev pinned changed age
  lock="$root/$SETUP_SUBDIR/flake.lock"
  owner="$(repository_owner "$root")"
  while read -r name node; do
    IFS=$'\t' read -r type repo_owner repo rev pinned < <(jq -r --arg node "$node" \
      '.nodes[$node] | [.original.type, .original.owner // "-", .original.repo // "-", .locked.rev // "-", .locked.lastModified // 0] | @tsv' "$lock")
    [[ "$type" == github && "$repo_owner" != "$owner" && "$rev" != - ]] || continue
    changed="$(git -C "$root" log -1 --format=%ct -S"$rev" -- "$SETUP_SUBDIR/flake.lock" 2>/dev/null || true)"
    # A shallow clone may not reach the commit that introduced this revision.
    [[ -n "$changed" ]] ||
      changed="$(git -C "$root" log -1 --format=%ct -- "$SETUP_SUBDIR/flake.lock" 2>/dev/null || true)"
    [[ -n "$changed" ]] || continue
    age=$((changed - pinned))
    ((age >= 0)) || age=0
    ((age < COOLDOWN_DAYS * 86400)) || continue
    printf -- '- %s: %s days (pinned %s, %s)\n' \
      "$name" "$(format_days "$age")" "${rev:0:7}" "$(date -u -d "@$pinned" +%F)"
  done < <(jq -r '.nodes.root.inputs | to_entries[] | select(.value | type == "string") | "\(.key) \(.value)"' "$lock")
}

# Prints "- name: old -> new" for packages whose version went backwards
# between two generations. A downgrade means a revert or a bad pin.
audit_downgrades() {
  local before="$1" after="$2" line name old new
  [[ -n "$before" && -n "$after" && "$before" != "$after" ]] || return 0
  while IFS= read -r line; do
    [[ "$line" == *" → "* ]] || continue
    name="${line%%:*}"
    old="${line#*: }"
    old="${old%% →*}"
    new="${line##*→ }"
    [[ "$old" != "$new" && "$old" != ∅ && "$new" != ∅ ]] || continue
    [[ "$(printf '%s\n%s\n' "$old" "$new" | sort -V | head -1)" == "$new" ]] || continue
    printf -- '- %s: %s -> %s\n' "$name" "$old" "$new"
  done < <(nix store diff-closures "$before" "$after" 2>/dev/null |
    sed 's/\x1b\[[0-9;]*m//g; s/, [-+]\?[0-9.]*[[:space:]][KMGT]\?i\?B$//')
}

# Prints pending Debian upgrades that come from the security archive, which
# `cros sync` would install. Simulating an upgrade needs neither root nor
# network; the package lists are already on disk.
audit_debian() {
  LC_ALL=C apt-get -s full-upgrade 2>/dev/null |
    sed -n '/^Inst .*[Ss]ecurity/ s/^Inst \([^ ]*\) .*/- \1/p'
}

cmd_audit() {
  local root changed generation current previous findings=0
  local -a soak downgrades security
  (($# == 0)) || die "audit takes no arguments"
  root="$(resolve_checkout "")"
  changed="$(git -C "$root" log -1 --format=%ct -- "$SETUP_SUBDIR/flake.lock" 2>/dev/null || true)"
  generation="$(stat -c %Y "$HM_PROFILE" 2>/dev/null || true)"
  if [[ -n "$changed" && -n "$generation" ]]; then
    log "flake.lock changed $(format_days $(($(date +%s) - changed))) days ago;" \
      "the Home Manager generation is $(format_days $(($(date +%s) - generation))) days old"
  elif [[ -n "$changed" ]]; then
    log "flake.lock changed $(format_days $(($(date +%s) - changed))) days ago"
  else
    log "the flake.lock history is unavailable (shallow clone?)"
  fi

  mapfile -t soak < <(audit_soak "$root")
  if ((${#soak[@]})); then
    printf 'Cooldown shortfall (third-party inputs younger than %s days when pinned):\n' "$COOLDOWN_DAYS"
    printf '%s\n' "${soak[@]}"
    findings=$((findings + ${#soak[@]}))
  fi

  current="$(readlink -f "$HM_PROFILE" 2>/dev/null || true)"
  previous="$(previous_generation)"
  mapfile -t downgrades < <(audit_downgrades "$previous" "$current")
  if ((${#downgrades[@]})); then
    printf 'Downgrades in the running generation:\n'
    printf '%s\n' "${downgrades[@]}"
    findings=$((findings + ${#downgrades[@]}))
  fi

  mapfile -t security < <(audit_debian)
  if ((${#security[@]})); then
    printf 'Debian security updates pending (%s):\n' "${#security[@]}"
    printf '%s\n' "${security[@]}"
    findings=$((findings + ${#security[@]}))
  fi

  if ((findings == 0)); then
    log "no findings"
    return 0
  fi
  log "$findings finding(s); 'cros sync' applies Debian updates, 'cros bump' moves inputs with the cooldown"
  return 1
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

# Prints "- input: old-rev (date) -> new-rev (date)" for each changed input.
print_input_changes() {
  jq -rn --slurpfile old "$1" --slurpfile new "$2" '
    def describe:
      (.locked // {}) as $l
      | "\(($l.rev // $l.narHash // $l.path // "?")[:12]) (\(
          if $l.lastModified then ($l.lastModified | strftime("%Y-%m-%d")) else "-" end))";
    $old[0].nodes as $o | $new[0].nodes as $n
    | ($o + $n | keys[] | select(. != "root"))
    | select($o[.].locked != $n[.].locked)
    | "- \(.): \(if $o[.] then ($o[.] | describe) else "added" end) -> \(
        if $n[.] then ($n[.] | describe) else "removed" end)"'
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

# Prints the GitHub owner of the checkout's origin, or nothing when the
# checkout has no origin or a non-GitHub one.
repository_owner() {
  local url
  url="$(git -C "$1" config --get remote.origin.url 2>/dev/null || true)"
  sed -n 's#.*github\.com[:/]\([^/]*\)/.*#\1#p' <<<"$url"
}

# Prints the bump plan for the given root inputs, one line each:
#   update NAME           update to the newest commit (no cooldown applies)
#   pin NAME URL DATE     pin to the input's newest commit from before CUTOFF
#   keep NAME DATE        keep the locked commit, already newer than that
# Cooldown applies to GitHub inputs not owned by the repository's own owner;
# path inputs are this repository, and the owner's inputs (sommelier-rs) are
# its maintainer's.
plan_cooldown() {
  local root="$1" cutoff="$2" lock owner name node type repo_owner repo ref locked reply sha date
  shift 2
  lock="$root/$SETUP_SUBDIR/flake.lock"
  owner="$(repository_owner "$root")"
  for name in "$@"; do
    node="$(jq -r --arg name "$name" '.nodes.root.inputs[$name] // empty | strings' "$lock")"
    [[ -n "$node" ]] || die "no input named $name (an input that follows another moves with it)"
    IFS=$'\t' read -r type repo_owner repo ref locked < <(jq -r --arg node "$node" \
      '.nodes[$node] | [.original.type, .original.owner // "-", .original.repo // "-", .original.ref // "-", .locked.lastModified // 0] | @tsv' "$lock")
    if [[ -z "$cutoff" || "$type" != github || "$repo_owner" == "$owner" ]]; then
      echo "update $name"
      continue
    fi
    reply="$(curl -fsSL "https://api.github.com/repos/$repo_owner/$repo/commits?until=$cutoff&per_page=1$([[ "$ref" == - ]] || echo "&sha=$ref")")" ||
      die "could not look up $repo_owner/$repo's commits before $cutoff (GitHub API); try again later"
    sha="$(jq -r '.[0].sha // empty' <<<"$reply")"
    date="$(jq -r '.[0].commit.committer.date // empty' <<<"$reply")"
    [[ -n "$sha" && -n "$date" ]] || die "$repo_owner/$repo has no commit before $cutoff"
    if (($(date -u -d "$date" +%s) > locked)); then
      echo "pin $name github:$repo_owner/$repo/$sha ${date%%T*}"
    else
      echo "keep $name $(date -u -d "@$locked" +%F)"
    fi
  done
}

cmd_bump() {
  local root head lock previous before after inputs packages workarounds message
  local cutoff argument plan action name url when names=() update=() overrides=()
  cutoff="$(date -u -d "$COOLDOWN_DAYS days ago" +%Y-%m-%dT%H:%M:%SZ)"
  for argument in "$@"; do
    case "$argument" in
      --no-cooldown) cutoff="" ;;
      -*) die "unknown bump option: $argument" ;;
      *) names+=("$argument") ;;
    esac
  done
  root="$(resolve_checkout "")"
  require_clean_main "$root"
  # The commit comes last; fail now rather than after the update and apply.
  git -C "$root" var GIT_COMMITTER_IDENT >/dev/null 2>&1 ||
    die "cros bump commits flake.lock; set your Git identity first: git config --global user.name/user.email"
  head="$(git -C "$root" rev-parse HEAD)"
  # Start from the published lock; otherwise `publish` cannot fast-forward.
  git -C "$root" pull --ff-only origin main ||
    die "main cannot fast-forward to origin/main; reconcile them, then run 'cros bump' again"
  lock="$root/$SETUP_SUBDIR/flake.lock"
  # Until the commit, a failure puts the previous lock back.
  guard_file "$lock" "restored the previous flake.lock; run 'cros apply' if the new one was activated"
  previous="$GUARDED_COPY"
  before="$(readlink -f "$HM_PROFILE" 2>/dev/null || true)"

  # Every direct input unless some are named; followers move with their input.
  ((${#names[@]})) ||
    mapfile -t names < <(jq -r '.nodes.root.inputs | to_entries[] | select(.value | type == "string") | .key' "$lock")
  plan="$(plan_cooldown "$root" "$cutoff" "${names[@]}")"
  while read -r action name url when; do
    case "$action" in
      update) update+=("$name") ;;
      pin)
        overrides+=(--override-input "$name" "$url")
        log "cooldown: $name at ${url##*/} ($when)"
        ;;
      keep) log "cooldown: $name keeps its locked commit ($url), newer than the cutoff" ;;
    esac
  done <<<"$plan"
  # `nix flake update` without names would update every input.
  if ((${#update[@]})); then
    nix flake update "${NIX_FLAGS[@]}" "${update[@]}" "${overrides[@]}" --flake "$(flake_dir "$root")"
  elif ((${#overrides[@]})); then
    nix flake lock "${NIX_FLAGS[@]}" "${overrides[@]}" --flake "$(flake_dir "$root")"
  fi
  if cmp -s -- "$previous" "$lock"; then
    log "flake inputs are already current"
    if [[ "$(git -C "$root" rev-parse HEAD)" != "$head" ]]; then
      log "applying the commits pulled from origin/main"
      nix run "${NIX_FLAGS[@]}" "$(flake_dir "$root")#cros" -- apply ||
        die "apply failed with the pulled commits"
    else
      print_reminders "$root"
    fi
    return 0
  fi
  CROS_NO_REMINDERS=1 nix run "${NIX_FLAGS[@]}" "$(flake_dir "$root")#cros" -- apply ||
    die "apply failed with the updated inputs"
  after="$(readlink -f "$HM_PROFILE" 2>/dev/null || true)"

  inputs="$(print_input_changes "$previous" "$lock")"
  packages="$(print_package_changes "$before" "$after")"
  workarounds="$(print_workarounds "$root")"
  message="chore(chromeos-dev-setup): update flake inputs"$'\n\nInputs:\n'"$inputs"
  if [[ -n "$cutoff" ]]; then
    message+=$'\n\n'"Cooldown: third-party inputs at their newest commit before ${cutoff%%T*} ($COOLDOWN_DAYS days)."
  fi
  if [[ -n "$packages" ]]; then
    message+=$'\n\nPackages:\n'"$packages"
  fi
  printf '%s\n' "$message"
  [[ -z "$workarounds" ]] || printf '\n%s\n' "$workarounds"
  git -C "$root" commit --quiet -m "$message" -- "$SETUP_SUBDIR/flake.lock"
  keep_file
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
  nix-collect-garbage --delete-older-than "${KEEP_GENERATIONS_DAYS}d"
  nix store optimise
}

cmd_reset() {
  local hard=0
  case "${1:-}" in
    "") ;;
    --hard) hard=1 ;;
    *) die "unknown reset option: $1" ;;
  esac
  local runtime="${XDG_RUNTIME_DIR:-/run/user/$UID}" units
  if ((hard)); then
    log "restarting desktop services; running GUI apps will close"
    mapfile -t units < <(loaded_units "${SESSION_UNITS[@]}")
    ((${#units[@]} == 0)) || systemctl --user stop "${units[@]}"
    rm -f "$runtime/wayland-1.lock" "$runtime/wayland-2.lock" "$runtime/wayland-1" "$runtime/wayland-2"
  fi
  start_desktop_services
}

# Prints the given units that systemd has loaded. A unit that does not exist
# (for example one a local.nix turned off) would make systemctl fail.
loaded_units() {
  local unit
  for unit in "$@"; do
    if [[ "$(systemctl --user show --property=LoadState --value "$unit" 2>/dev/null || true)" == loaded ]]; then
      printf '%s\n' "$unit"
    fi
  done
}

# Starting an inactive unit never disturbs running apps.
start_desktop_services() {
  local units
  log "starting inactive desktop services"
  systemctl --user daemon-reload
  mapfile -t units < <(loaded_units earlyoom.service "${SESSION_UNITS[@]}")
  ((${#units[@]} > 0)) || return 0
  systemctl --user reset-failed "${units[@]}"
  systemctl --user start "${units[@]}"
}

main() {
  local command="${1:-}" argument
  (($#)) && shift
  # `cros <command> --help` shows help instead of running the command.
  for argument in "$@"; do
    if [[ "$argument" == -h || "$argument" == --help ]]; then
      usage
      return 0
    fi
  done
  case "$command" in
    -h | --help | help) usage ;;
    __commands) list_commands ;;
    "")
      usage
      exit 2
      ;;
    *)
      if ! is_command "$command"; then
        usage >&2
        die "unknown command: $command"
      fi
      "cmd_$command" "$@"
      ;;
  esac
}

main "$@"
