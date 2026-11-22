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
  setup.bash          bootstrap: prerequisites, Nix, then `cros apply`
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

Git identity is not host data: it lives in the user-owned `~/.gitconfig`;
Home Manager keeps shared Git settings in `~/.config/git/config`.

## Builds

The flake is evaluated from the checkout itself:
`git+file://<checkout>?dir=chromeos-dev-setup`. Consequences:

- Evaluation is pure (`system = "x86_64-linux"`, no `--impure`, no host paths).
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
from the installed one. The version embeds a hash of the package tree.

- `Depends`: `uidmap cros-im btrfs-progs curl ca-certificates git xz-utils python3`
- `Conflicts`: `vim vim-tiny command-not-found` (removes them)
- Files live in vendor directories so they are package-owned, not conffiles:
  `/usr/lib/udev/rules.d/51-cros-android.rules`,
  `/usr/lib/tmpfiles.d/00-cros-tmp.conf` (sorts before systemd's `tmp.conf`, so
  its `/tmp` line wins), `/usr/lib/systemd/system/tmp.mount.d/50-cros-disk-backed.conf`.
- `postinst configure`: creates the btrfs subvolume `/var/lib/tmp` with a
  MemTotal/2 quota and no-CoW, then reloads systemd and udev when they are
  running.
- `postrm`: reloads systemd and udev; it never deletes `/var/lib/tmp`.

Verified in a `debian:trixie` container: `Depends` installs, `Conflicts`
removes `vim-tiny`, `dpkg -S` reports ownership, a file dropped in a newer
version is removed on upgrade and restored on downgrade, `apt purge` leaves
nothing, and the `00-` tmpfiles name wins over systemd's `tmp.conf`.

Only user-specific or Nix-level steps remain in `setup.bash`: the `render`
group, installing Nix, and ensuring `trusted-users` includes the user (restart
`nix-daemon` only when that line changes). It writes `GIT_USER_NAME`/`GIT_USER_EMAIL`
to `~/.gitconfig` once, and refuses the README's example values before changing
anything.

## The `cros` CLI

| Command | Effect |
| --- | --- |
| `cros sync` | Requires a clean `main`; `git pull --ff-only`, `apt-get update/full-upgrade/autoremove`, `determinate-nixd upgrade`, then `apply`. |
| `cros apply` | Add sparse paths, install the system package if changed, refresh host data, build and activate Home Manager, start inactive desktop services. If the build or activation fails (or is interrupted), the previous host data is restored, so the machine keeps following its previous checkout. |
| `cros check` | Build the system package and the Home Manager configuration without installing or activating anything, then report whether this machine already runs that build (installed package version, active Home Manager generation). |
| `cros bump [input…]` | Maintainer: requires a clean `main`; `git pull --ff-only` (so the commit can be published), `nix flake update [input…]`, `apply`, print input and package-version changes and workaround status, commit only `flake.lock`. Does not push. |
| `cros publish` | Maintainer: requires a clean `main` that is ahead of and descends from `origin/main`; push. |
| `cros clean` | Delete generations older than 14 days (recent ones stay for rollback), collect garbage, optimise the store. |
| `cros reset [--hard]` | Start inactive desktop services; `--hard` restarts them (closes GUI apps). |

One table in `cros.bash` lists the commands; `cros --help`, dispatch, and the
zsh completion (`cros/_cros`, via `cros __commands`) all read it. `--help`
after any command prints help without running the command.

`apply` and `bump` end with reminders. When the applied checkout is not on
`main` (for example a worktree applied with `apply --checkout` for testing),
the reminder names the command that returns to the primary checkout. On
maintainer machines on `main`, reminders report unpushed `bump` commits or a
last lock change older than 14 days.

## Home Manager changes

- Desktop entries: `xdg.dataFile."applications"` links each file from the
  profile (`recursive = true`); icons are not copied. Verified live: garcon
  watches `~/.local/share/applications` but not the Nix profile, symlinked
  entries appear, retargeted links update the launcher immediately, removed
  links disappear, and icons resolve from `XDG_DATA_DIRS`.
- Login shell: Debian `bash` stays the login shell. The managed `~/.bashrc`
  execs the Nix `zsh` for interactive shells (with `SHELL` exported and login
  mode preserved); if zsh is missing or broken, the terminal still opens in bash.
  `CROS_NO_ZSH=1` skips the switch.
- Codex Desktop runs with `CODEX_OZONE_PLATFORM=wayland`; under X11 the
  wayland-first `GTK_IM_MODULE` crashes it on first text input.

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
| Git identity | `~/.gitconfig` | Nix-managed via host data |
| Per-user customization | `~/.config/cros/host/local.nix` | none |
| Packages | all shared for now | split shared/personal |
| In-repo packages | relative path | GitHub URL + lock bump |
| Root configuration | local Debian package | marker files; system-manager; imperative script; manifest file; systemd-confext; Ansible |
| Login shell | bash execs zsh | `chsh` to the Nix zsh (terminal fails to open if the profile breaks) |
| Desktop entries | Home Manager links | `rm -rf` + copy (deletes unrelated entries); own manifest |
| Update flow | `bump` commits, `publish` pushes | push inside `bump` |
| Reminders | in `cros` output only | shell start, prompt, desktop notifications |
| CI | tests + pure eval + deb build | full build; none |
| W1 trusted-users | ensured in code | note only |
| W2 GitHub rate limit | README troubleshooting (scope-less token, 0600 include) | copying the `gh` token automatically |
| Removed as dead | opencode-desktop overlay, `GSETTINGS_SCHEMA_DIR`, `~/.codex/packages/standalone` link, `CODEX_LINUX_*` variables, OpenSpec old-layout fallback, W3/W4 notes, GSSAPI cleanup, root-shell repair, `nixpkgs/orca-deb-bin`, `nixpkgs/orca-ide`, commented legacy blocks | |
| Kept | `sommelier-x@1` `SOMMELIER_SCALE=1.0` (Debian sets 0.5), adb rule, vim removal, `remoteMobileControl.enable` (Desktop owns remote control) | |
