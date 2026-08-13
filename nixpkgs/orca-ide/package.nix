{ lib
, appimageTools
, fetchurl
, procps
, stdenv
}:

let
  version = "1.4.179";
  sources = {
    "x86_64-linux" = {
      url = "https://github.com/stablyai/orca/releases/download/v1.4.179/orca-linux.AppImage";
      hash = "sha256-B4CEhW22bSmya1dguIAo3pWv/NdxQxNZ7uNpJCl7EN8=";
    };
    "aarch64-linux" = {
      url = "https://github.com/stablyai/orca/releases/download/v1.4.179/orca-linux-arm64.AppImage";
      hash = "sha256-Toa2/8DymRlMDfuGsRDnhRdNk6XTGo4nFthrfFa+WRQ=";
    };
  };
  source = sources.${stdenv.hostPlatform.system} or (
    throw "orca-ide: unsupported system ${stdenv.hostPlatform.system}"
  );
  src = fetchurl source;
  extracted = appimageTools.extract {
    pname = "orca-ide";
    inherit version src;
  };
in
appimageTools.wrapType2 {
  pname = "orca-ide";
  inherit version src;

  # Orca enumerates processes with `ps`; the default AppImage FHS does not
  # include procps.
  extraPkgs = _: [ procps ];

  # The AppImage FHS hides Crostini's host browser integration files. Make
  # temporary overlays for their parent directories first because the FHS root
  # is read-only and bubblewrap otherwise cannot create file-bind destinations.
  # try-bind keeps the package portable on Linux systems without ChromeOS
  # integration.
  extraBwrapArgs = [
    "--overlay-src /usr/bin"
    "--tmp-overlay /usr/bin"
    "--overlay-src /usr/share/applications"
    "--tmp-overlay /usr/share/applications"
    "--ro-bind-try /usr/bin/garcon-url-handler /usr/bin/garcon-url-handler"
    "--ro-bind-try /usr/share/applications/garcon_host_browser.desktop /usr/share/applications/garcon_host_browser.desktop"
  ];

  # AppImages do not export their desktop metadata through wrapType2, so copy
  # the upstream launcher and icon into the Nix profile for desktop discovery.
  extraInstallCommands = ''
    install -Dm444 ${extracted}/orca-ide.desktop \
      $out/share/applications/orca-ide.desktop
    install -Dm444 ${extracted}/usr/share/icons/hicolor/512x512/apps/orca-ide.png \
      $out/share/icons/hicolor/512x512/apps/orca-ide.png
    substituteInPlace $out/share/applications/orca-ide.desktop \
      --replace-fail "Exec=AppRun --no-sandbox %U" "Exec=orca-ide --no-sandbox %U"
  '';

  meta = with lib; {
    description = "AI orchestrator for parallel agentic development";
    homepage = "https://onorca.dev";
    license = licenses.mit;
    mainProgram = "orca-ide";
    platforms = [ "x86_64-linux" "aarch64-linux" ];
  };
}
