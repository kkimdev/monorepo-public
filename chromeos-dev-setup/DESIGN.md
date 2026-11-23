# chromeos-dev-setup design

This document records how the setup is structured and why. It was decided
topic by topic in November 2026; the "Decisions" section lists each choice
with the alternatives that lost.

## Requirements

- One maintainer writes this repository; other users only consume `main`.
  Pushing to `main` is a release.
- Machines need not be identical. The maintainer refreshes dependencies and
  syncs; other users run whatever `main` pins.
- Updating must be one command for users and an easy periodic routine for the
  maintainer.
- Platform: Google's default Debian on ChromeOS (Crostini/Baguette), x86_64.
- Keep one owner for each piece of state, prefer platform-native mechanisms,
  and make removal of a setting remove it from every machine.

## Layout

```text
chromeos-dev-setup/
  setup.bash          bootstrap: clone when piped, prerequisites, Nix,
                      `cros apply`, then a Debian upgrade
  flake.nix           pure flake; inputs include `host` (per-user data)
  flake.lock
  home.nix            Home Manager configuration shared by all users
  host-example/       example host data used by CI and pure evaluation
  cros/               the `cros` CLI and its zsh completion (Nix package,
                      installed by Home Manager)
  system/             local Debian package `cros-dev-system`
  codex-wrapper       Codex model-policy wrapper (installed by Home Manager)
  patch-orca.py       Orca desktop-identity patch
  workarounds.json    upstream workarounds and their removal conditions
  tests/
nixpkgs/kakaotalk/    referenced by relative path
.github/workflows/chromeos-dev-setup.yml
```

## Per-user host data

`~/.config/cros/host/` is outside the repository and is injected as the flake
input `host` (`flake = false`):

- `host.json`, written only by `cros apply` (atomic replace):
  `username`, `homeDirectory`, `checkout` (repository root), `gtk3Cache`,
  `gtk4Path`, `maintainer` (bool, preserved across rewrites).
- `local.nix` (optional): a Home Manager module for per-user additions.

The repository default `path:./host-example` makes evaluation pure and gives CI
a user named `ci`. Every build passes
`--override-input host path:$HOME/.config/cros/host`; Home Manager's activation
refuses a USER/HOME mismatch, so a build that forgets the override cannot be
activated by accident.

Git identity is not managed: users set it in their own `~/.gitconfig`. Only
`cros bump` needs it (it commits), and it checks before changing anything.
Home Manager keeps shared Git settings in `~/.config/git/config`.

## Builds

The flake is evaluated from the checkout itself:
`git+file://<checkout>?dir=chromeos-dev-setup`. Consequences:

- Evaluation is pure (`system = "x86_64-linux"`, no `--impure`, no host paths).
- There is one nixpkgs: the official `NixOS/nixpkgs` `nixpkgs-unstable` that
  llm-agents pins (`nixpkgs.follows = "llm-agents/nixpkgs"`), so llm-agents'
  binary cache matches and no second package set is installed. It moves with
  `cros bump llm-agents`; `cros bump nixpkgs` matches nothing.
- `flake.lock` is read and updated in place; nothing is copied to a staging
  directory.
- `nixpkgs/kakaotalk` is the input `path:../nixpkgs/kakaotalk`, so a change to
  the package ships with the commit that makes it, without a lock bump.
- New files must be tracked by Git (`git add`) to be visible to Nix.
- Sparse checkouts must include every repository path the flake reads.
  `cros apply` derives them from the relative `path` inputs in `flake.lock`, so
  a new in-repo package needs no list update. Shallow, sparse clones evaluate
  correctly, and `cros` itself builds before those paths exist (inputs are
  fetched lazily) (verified).
- Flake references are plain directories (`<checkout>/chromeos-dev-setup`),
  which Nix resolves to `git+file://…?dir=chromeos-dev-setup`; no URL is built
  by hand.

## System configuration: local Debian package

Root-owned configuration is the local package `cros-dev-system`, built by Nix
(`packages.x86_64-linux.system-deb`, `dpkg-deb`) and installed with
`apt-get install --allow-downgrades <deb>` whenever the built version differs
from the installed one. The version embeds a hash of `system/` (the package
tree and the recipe that builds it).

- `Depends`: `uidmap cros-im btrfs-progs e2fsprogs curl ca-certificates git xz-utils python3`
- `Conflicts`: `vim vim-tiny command-not-found` (removes them)
- Files live in vendor directories so they are package-owned, not conffiles:
  `/usr/lib/udev/rules.d/51-cros-android.rules`,
  `/usr/lib/tmpfiles.d/00-cros-tmp.conf` (sorts before systemd's `tmp.conf`, so
  its `/tmp` line wins), `/usr/lib/systemd/system/tmp.mount.d/50-cros-disk-backed.conf`.
- Debian security and stable updates: Crostini's image lists only
  `<codename> main`, so security fixes would wait for point releases.
  `/usr/lib/cros-dev-system/debian-sources` writes
  `/etc/apt/sources.list.d/cros-dev-debian.list` with `<codename>-security` and
  `<codename>-updates` (codename from `/etc/os-release`), skipping a suite that
  another enabled binary source already provides (a one-line `deb` entry, or a
  deb822 stanza with `Types: deb` that is not `Enabled: no`; `deb-src` entries
  and comments do not count). `postinst` runs it, and the apt hook
  `/etc/apt/apt.conf.d/50cros-dev-system` (`APT::Update::Pre-Invoke`, never
  fatal) reruns it before every update, so a Debian release upgrade follows
  automatically; apt reads its sources before the hook, so a change applies from
  the next update.
- The same apt file sets `APT::Keep-Downloaded-Packages "false"`: `apt-get`
  (used by `cros sync`) deletes downloaded `.deb` files after a successful
  install, as `apt` already does, so `/var/cache/apt/archives` does not grow.
- `postinst configure`: creates the btrfs subvolume `/var/lib/tmp` with a
  MemTotal/2 quota and no-CoW, writes the Debian sources, then reloads systemd
  and udev when they are running.
- `postrm`: removes the generated sources file and reloads systemd and udev; it
  never deletes `/var/lib/tmp`.

Verified in a `debian:trixie` container: `Depends` installs, `Conflicts`
removes `vim-tiny`, `dpkg -S` reports ownership, a file dropped in a newer
version is removed on upgrade and restored on downgrade, `apt purge` leaves
nothing, and the `00-` tmpfiles name wins over systemd's `tmp.conf`.

Only user-specific or Nix-level steps remain in `setup.bash`: the `render`
group, installing Nix, and ensuring `trusted-users` includes the user (restart
`nix-daemon` only when that line changes). It also installs `cros-im` up front,
because `cros apply` records its GTK paths before installing the system
package, and after `cros apply` it upgrades Debian once (`update`,
`full-upgrade`, `autoremove`), when the security and updates suites are in
place.

Piped into bash (`curl … | bash`, the README's install command), it has no
file of its own: after the user, architecture, and device checks it installs
git if missing, clones the repository shallow and sparse to
`~/workspaces/monorepo-public` (or reuses that checkout), and reruns itself
from there. The body is a `main` function called on the last line, so bash
has read the whole script before any command can read the piped stdin.

## The `cros` CLI

| Command | Effect |
| --- | --- |
| `cros sync` | Requires a clean `main`; `git pull --ff-only`, `apt-get update/full-upgrade/autoremove`, `determinate-nixd upgrade`, then `apply`. |
| `cros apply` | Add sparse paths, build the system package, refresh host data, build Home Manager, then install the system package if changed, activate Home Manager, link that generation's GPU drivers if `/run/opengl-driver` points elsewhere (Home Manager's `non-nixos-gpu-setup`, which needs sudo; Home Manager itself only warns), and start inactive desktop services (units systemd has not loaded are skipped). A failed build changes nothing on the system; if a build or activation fails (or is interrupted), the previous host data is restored, so the machine keeps following its previous checkout. |
| `cros check` | Build the system package and the Home Manager configuration without installing or activating anything, then report whether this machine already runs that build (installed package version, active Home Manager generation, GPU driver link). |
| `cros audit` | Report what drifted or got worse without a build: third-party inputs the lock took before the 7-day cooldown, package downgrades between the previous and active generation, and Debian security updates `sync` has not installed yet. Exits 1 when it finds any. Uses only local data. |
| `cros bump [--no-cooldown] [input…]` | Maintainer: requires a clean `main` and a Git identity; `git pull --ff-only` (so the commit can be published), update the inputs with a 7-day cooldown (below), `apply`, print input and package-version changes and workaround status, commit only `flake.lock`. When inputs are already current but the pull brought commits, it applies them. Does not push. |
| `cros publish` | Maintainer: requires a clean `main` that is ahead of and descends from `origin/main`; push. |
| `cros clean` | Delete generations older than 14 days (recent ones stay for rollback), collect garbage, optimise the store. |
| `cros reset [--hard]` | Start inactive desktop services; `--hard` restarts them (closes GUI apps). |

One table in `cros.bash` lists the commands; `cros --help`, dispatch, and the
zsh completion (`cros/_cros`, via `cros __commands`) all read it. `--help`
after any command prints help without running the command.

Update cooldown: `bump` moves each third-party GitHub input only to its
newest commit at least 7 days old, found through the GitHub commits API
(`until=<cutoff>` on the input's branch) and locked with
`--override-input`, which keeps the input's URL in `flake.nix`. An input whose
locked commit is already newer stays. Path inputs (this repository) and GitHub
inputs owned by the owner of `origin` (sommelier-rs) update immediately, and
`--no-cooldown` skips the cooldown for urgent fixes. nixpkgs follows
llm-agents, so it is cooled down with it. Debian packages have no cooldown:
stable updates are conservative and security fixes should not wait.

`apply` and `bump` end with reminders. When the applied checkout is not on
`main` (for example a worktree applied with `apply --checkout` for testing),
the reminder names the command that returns to the primary checkout. On
maintainer machines on `main`, reminders report unpushed `bump` commits or a
last lock change older than 14 days.

Audit: `apply` prints reminders only when it runs, so a machine can sit on a
stale or unsoaked lock without anyone noticing. `audit` reports, from local
data only:

- Cooldown shortfall: for each third-party GitHub input, the soak between the
  commit it is locked at and the commit that last changed that revision in
  `flake.lock`. Under 7 days means the gate did not apply — `keep` and
  `--no-cooldown` leave no trace in the lock, and bound inputs (llm-agents
  carries nixpkgs) move newer than their own cooldown. Git history is the
  only record; a shallow clone falls back to the last lock commit.
- Downgrades: packages whose version went backwards between the previous and
  the active Home Manager generation (`nix store diff-closures`, compared
  with `sort -V`). A downgrade means an upstream revert or a bad pin, which
  is exactly the shape of the xz incident.
- Debian: pending upgrades from the security archive, from `apt-get -s` (no
  root, no network).

`audit` deliberately makes no network calls and does not scan for CVEs.
Advisory feeds are high volume and map poorly onto Nix store paths (see
Decisions), so the useful outward signal is a curated one: a human reports a
problem, and `bump`/`sync` act on it. What `audit` adds is the ability to
notice that the machine drifted, and to date an incident ("openssl moved to
3.5.1 on this generation"), before deciding to roll back.

## Home Manager changes

- Desktop entries: `xdg.dataFile."applications"` links each file from the
  profile (`recursive = true`). Verified live: garcon watches
  `~/.local/share/applications` but not the Nix profile, symlinked entries
  appear, retargeted links update the launcher immediately, and removed links
  disappear.
- Icons: garcon (`vm_tools/garcon/icon_finder.cc`) searches its
  `XDG_DATA_DIRS` and always `~/.local/share`, under `icons/{gnome,hicolor}`
  and `pixmaps`. Its `XDG_DATA_DIRS` drop-in takes effect only when garcon
  restarts, which on a fresh install comes after setup, so the profile's
  `share/icons` and `share/pixmaps` are linked into `~/.local/share` too and
  icons show at once.
- Login shell: Debian `bash` stays the login shell. The managed `~/.bashrc`
  execs the Nix `zsh` for interactive shells (with `SHELL` exported and login
  mode preserved); if zsh is missing or broken, the terminal still opens in bash.
  `CROS_NO_ZSH=1` skips the switch.
- PATH: `home.sessionPath` (absolute paths) is the one list of user
  directories. Shells get it from Home Manager; the cros-garcon drop-in (apps
  the ChromeOS launcher starts) and the `local-services` unit start PATH with
  it, followed by the Nix profile `bin` and each unit's system directories.
  Units pick up a change when they restart (cros-garcon: next Linux restart).
- Desktop apps come from maintained sources: OpenAI's official Linux ChatGPT
  (Codex) app and the Antigravity app from llm-agents, the Antigravity IDE from
  nixpkgs. Small `wrapApp` wrappers add what Crostini needs: `NIXOS_OZONE_WL=1`
  so ChatGPT runs on Wayland (under X11 the wayland-first `GTK_IM_MODULE`
  crashes it on first text input), `CHROME_BIN` pointing Antigravity's browser
  agent at the installed Chromium, and the IDE's existing data directory
  `~/.antigravity-ide`. Desktop entries run binaries by name, so they use the
  wrappers.

## Workarounds

`workarounds.json` lists each upstream workaround with its location, reason,
and removal condition (`github-pr`/`github-issue` with repo and number, or
`manual`). `cros bump` prints their status (via `gh` when available).

## CI

GitHub Actions on pushes and pull requests touching `chromeos-dev-setup/` or
`nixpkgs/kakaotalk/`: unit tests, pure evaluation of
`homeConfigurations.ci.activationPackage.drvPath`, and `nix build .#system-deb`.
A full Home Manager build is out of scope for hosted runners.

## Decisions

| Topic | Decision | Rejected alternatives |
| --- | --- | --- |
| Platform | Google's Debian | NixOS Baguette image (community-maintained, IME/GPU unverified, harder install) |
| Structure | One pure flake, host data via input override | staging copy + `host.json` (current); wrapper user flake (two layers, repo `nixConfig` ignored); `getEnv` impurity; per-user locks |
| System arch | `x86_64-linux` fixed | host-provided system |
| Crostini detection | removed; always Crostini | eval-time `pathExists` (impure) |
| GTK paths | detected by `cros apply`, passed in host data | constants; runtime detection in shells |
| Git identity | left to the user (`~/.gitconfig`) | Nix-managed via host data; written once by `setup.bash` |
| Per-user customization | `~/.config/cros/host/local.nix` | none |
| Packages | all shared for now | split shared/personal |
| In-repo packages | relative path | GitHub URL + lock bump |
| Root configuration | local Debian package | marker files; system-manager; imperative script; manifest file; systemd-confext; Ansible |
| Login shell | bash execs zsh | `chsh` to the Nix zsh (terminal fails to open if the profile breaks) |
| Desktop entries | Home Manager links | `rm -rf` + copy (deletes unrelated entries); own manifest |
| Update flow | `bump` commits, `publish` pushes | push inside `bump` |
| Update cooldown | `bump` pins each third-party GitHub input to its newest commit at least 7 days old (never older than the locked one) | skip inputs whose newest commit is too new (daily inputs would never update); none |
| Drift detection | `audit` reads local data only: lock history (soak at pin time), generations (downgrades), apt (pending security updates) | a weekly CI `nix flake update` (nobody applies or tests the result on this machine, which is the only deployment target); CVE scanners in `audit` (`vulnix`, `grype`, `syft`, `osv-scanner`: CPE/ecosystem mapping does not describe Nix closures, so false positives drown the signal); advisory feeds (hundreds of open nixpkgs security issues, no reliable package mapping) |
| nixpkgs source | `nixpkgs.follows = "llm-agents/nixpkgs"`: one nixpkgs and numtide's cache | FlakeHub `nixpkgs-weekly` (breaks the `follows` design; soak becomes 0–7 days, averaging 3.5); a separate nixpkgs pin (cache misses for llm-agents) |
| home-manager source | flake input | `pkgs.home-manager` (CLI only, no module tree, and months behind the input) |
| nix-index-database source | flake input | nixpkgs (unavailable: it is per-revision index data, not a package); `nix-index --build` locally (hours and large memory) |
| Debian upgrades | `setup.bash` once, then `cros sync` (manual), with security and updates suites | `unattended-upgrades` (security only, or everything) |
| Reminders | in `cros` output only | shell start, prompt, desktop notifications |
| CI | tests + pure eval + deb build | full build; none |
| W1 trusted-users | ensured in code | note only |
| W2 GitHub rate limit | README troubleshooting (scope-less token, 0600 include) | copying the `gh` token automatically |
| Removed as dead | opencode-desktop overlay, `GSETTINGS_SCHEMA_DIR`, `~/.codex/packages/standalone` link, `CODEX_LINUX_*` variables, OpenSpec old-layout fallback, W3/W4 notes, GSSAPI cleanup, root-shell repair, `nixpkgs/orca-deb-bin`, `nixpkgs/orca-ide`, commented legacy blocks | |
| Kept | `sommelier-x@1` `SOMMELIER_SCALE=1.0` (Debian sets 0.5), adb rule, vim removal, `remoteMobileControl.enable` (Desktop owns remote control) | |
