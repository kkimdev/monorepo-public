# Official Flex media is content-locked independently of the NixOS live system.
{ pkgs }:
let
  lock = builtins.fromJSON (builtins.readFile ../media/media.json);
  fetch = name: item: pkgs.fetchurl {
    inherit name;
    url = item.url;
    sha256 = item.sha256;
  };
in
assert lock.format == 2;
{
  flex-archive = fetch "${lock.flex.file}.zip" lock.flex;
}
