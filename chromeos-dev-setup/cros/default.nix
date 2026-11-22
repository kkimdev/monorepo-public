{ symlinkJoin
, writeShellApplication
, coreutils
, diffutils
, findutils
, git
, gnugrep
, gnused
, jq
}:

let
  cli = writeShellApplication {
    name = "cros";
    # System tools (nix, sudo, apt-get, dpkg, systemctl, gh) come
    # from the host PATH on purpose: they must match the running system.
    runtimeInputs = [ coreutils diffutils findutils git gnugrep gnused jq ];
    text = builtins.readFile ./cros.bash;
  };
in
# The CLI plus its zsh completion, which Home Manager's zsh finds in the profile.
symlinkJoin {
  name = "cros";
  paths = [ cli ];
  postBuild = ''
    install -Dm644 ${./_cros} $out/share/zsh/site-functions/_cros
  '';
  meta = {
    description = "Apply, update, and publish the chromeos-dev-setup configuration";
    mainProgram = "cros";
  };
}
