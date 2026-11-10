#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ] || [[ "$1" != /* ]]; then
  echo "Usage: bash update.bash ABSOLUTE_SETUP_SCRIPT" >&2
  exit 2
fi
setup_script="$1"
if [ ! -f "$setup_script" ]; then
  echo "ERROR: setup script does not exist: $setup_script" >&2
  exit 1
fi

repo_dir="$(git -C "$(dirname -- "$setup_script")" rev-parse --show-toplevel)"
branch="$(git -C "$repo_dir" symbolic-ref --quiet --short HEAD || true)"
if [ "$branch" != main ]; then
  echo "ERROR: cros-update requires the main branch; current branch: ${branch:-detached HEAD}." >&2
  exit 1
fi
dirty_status="$(git -C "$repo_dir" status --porcelain --untracked-files=all)"
if [ -n "$dirty_status" ]; then
  echo "ERROR: cros-update requires a clean repository, including untracked files." >&2
  exit 1
fi

git -C "$repo_dir" pull --ff-only origin main
sudo apt-get update
sudo apt-get full-upgrade -y
sudo apt-get autoremove -y
sudo determinate-nixd upgrade
bash "$setup_script"
