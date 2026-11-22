{ writeShellApplication
, coreutils
, diffutils
, findutils
, git
, gnugrep
, gnused
, jq
, python3
}:

writeShellApplication {
  name = "cros";
  # System tools (nix, sudo, apt-get, dpkg, systemctl, gh) come
  # from the host PATH on purpose: they must match the running system.
  runtimeInputs = [ coreutils diffutils findutils git gnugrep gnused jq python3 ];
  text = builtins.readFile ./cros.bash;
  meta.description = "Apply, update, and publish the chromeos-dev-setup configuration";
}
