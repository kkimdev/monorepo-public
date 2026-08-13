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
  crostiniBrowserIntegration = stdenv.mkDerivation {
    pname = "crostini-garcon-browser-integration";
    version = "1";
    dontUnpack = true;
    installPhase = ''
      install -Dm755 /dev/stdin "$out/bin/garcon-url-handler" <<'EOF'
      #!/bin/sh
      exec /opt/google/cros-containers/bin/garcon --client --url "$@"
      EOF

      install -Dm644 /dev/stdin "$out/share/applications/garcon_host_browser.desktop" <<'EOF'
      [Desktop Entry]
      Name=Chrome OS Host Browser
      Exec=garcon-url-handler %U
      MimeType=x-scheme-handler/http;x-scheme-handler/https;x-scheme-handler/ftp;x-scheme-handler/mailto;
      Type=Application
      NoDisplay=true
      OnlyShowIn=Never
      EOF
    '';
  };
in
appimageTools.wrapType2 {
  pname = "orca-ide";
  inherit version src;

  # Orca enumerates processes with `ps`; the default AppImage FHS does not
  # include procps.
  # Keep the Crostini handler inside the FHS instead of overlaying /usr/bin:
  # the latter hides the FHS-provided ldconfig and breaks AppImage startup.
  extraPkgs = _: [ procps crostiniBrowserIntegration ];

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
