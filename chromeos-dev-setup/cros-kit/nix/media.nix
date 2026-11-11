# The signed index is retained because upstream replaces it in place.
{ pkgs }:
let
  lock = builtins.fromJSON (builtins.readFile ../media/media.json);
  fetch = name: item: pkgs.fetchurl {
    inherit name;
    url = item.url;
    sha256 = item.sha256;
  };
  packages = map (item:
    assert builtins.match "[A-Za-z0-9_.+~-]+\\.apk" item.filename != null;
    { name = item.filename; path = fetch item.filename item; }
  ) lock.alpine.packages;
in
assert lock.format == 1;
assert builtins.hashFile "sha256" ../media/alpine-index.tar.gz == lock.alpine.index_sha256;
{
  flex-archive = fetch "${lock.flex.file}.zip" lock.flex;
  alpine-iso = fetch lock.alpine.filename lock.alpine;
  alpine-repo = pkgs.runCommand "cros-kit-alpine-repo" {} ''
    mkdir -p "$out/x86_64"
    cp ${../media/alpine-index.tar.gz} "$out/x86_64/APKINDEX.tar.gz"
    ${pkgs.lib.concatMapStringsSep "\n" (item: ''
      ln -s ${item.path} "$out/x86_64/${item.name}"
    '') packages}
  '';
}
