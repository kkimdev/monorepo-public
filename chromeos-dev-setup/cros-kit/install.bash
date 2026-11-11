#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || ! $1 =~ ^(install|remove|status)$ ]]; then
  echo 'Usage: bash install.bash install|remove|status --disk /dev/DEVICE [--dry-run]' >&2
  exit 2
fi

work_dir=$(mktemp -d -t cros-kit.XXXXXXXX)
trap 'rm -rf -- "$work_dir"' EXIT
revision=${CROS_KIT_REV:-}
if [[ -z $revision ]]; then
  curl --fail --location --silent --show-error --retry 2 \
    https://api.github.com/repos/kkimdev/monorepo-public/commits/main -o "$work_dir/revision.json"
  revision=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["sha"])' "$work_dir/revision.json")
fi
if [[ ! $revision =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo 'CrOS Kit revision must be a full 40-character Git commit SHA.' >&2
  exit 2
fi
revision=${revision,,}
echo "Using CrOS Kit revision $revision"
source_url=https://raw.githubusercontent.com/kkimdev/monorepo-public/$revision/chromeos-dev-setup/cros-kit
for name in install.py compatibility.conf crdyboot-debug.efi crdyboot-debug.efi.sha256 rw-init.sh rw-init.sh.sha256 rootdev-compat.so rootdev-compat.so.sha256; do
  curl --fail --location --silent --show-error --retry 2 "$source_url/$name" -o "$work_dir/$name"
done
(cd "$work_dir" && sha256sum --check crdyboot-debug.efi.sha256 rw-init.sh.sha256 rootdev-compat.so.sha256)
sudo python3 "$work_dir/install.py" "$@"
