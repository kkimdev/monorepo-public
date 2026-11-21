{ lib
, stdenv
, fetchurl
, makeDesktopItem
, copyDesktopItems
, replaceVars
, wineWow64Packages
, nanum
, bash
, curl
, osslsigncode
, cacert
, librsvg
}:

let
  iconSrc = fetchurl {
    url = "https://upload.wikimedia.org/wikipedia/commons/e/e3/KakaoTalk_logo.svg";
    hash = "sha256-0N1j/BFQCtpUN3mnO8PRMpB5Xaf3Ozh7bqX+ME8kTZk=";
  };

  # Use wineWow64Packages.stable by default if available, otherwise fallback to wine
  wine = wineWow64Packages.stable;

  runner = replaceVars ./kakaotalk-runner {
    inherit wine nanum bash curl osslsigncode;
    # Trust anchors for the installer's Authenticode and timestamp chains.
    caBundle = "${cacert}/etc/ssl/certs/ca-bundle.crt";
    # Kakao overwrites this URL on release. Fetch only on first launch so a
    # vendor update cannot break an otherwise locked Home Manager build.
    installerUrl = "https://lk.kakaocdn.net/talkpc/talk/win32/x64/KakaoTalk_Setup.exe";
  };
in
stdenv.mkDerivation rec {
  pname = "kakaotalk-bin";
  version = "1.0.0"; # Launcher version; KakaoTalk installs and updates at runtime.

  dontUnpack = true;

  nativeBuildInputs = [ copyDesktopItems librsvg ];

  desktopItems = [
    (makeDesktopItem {
      # ChromeOS matches a native Wayland window to a launcher by comparing the
      # window's app_id with the desktop file ID, not StartupWMClass. Wine's
      # Wayland driver derives the app_id from the executable name, so the
      # desktop file must be kakaotalk.exe.desktop (ID "kakaotalk.exe").
      name = "kakaotalk.exe";
      exec = "kakaotalk %U";
      icon = "kakaotalk";
      desktopName = "KakaoTalk";
      genericName = "Messenger";
      comment = "Where people and the world come to get connected";
      categories = [ "Network" "InstantMessaging" ];
      # Used by X11/Xwayland window matching.
      startupWMClass = "kakaotalk.exe";
    })
  ];

  installPhase = ''
    runHook preInstall

    # Install the compiled runner script
    mkdir -p $out/bin
    cp ${runner} $out/bin/kakaotalk
    chmod +x $out/bin/kakaotalk

    # Install the high-quality SVG icon
    mkdir -p $out/share/icons/hicolor/scalable/apps
    cp ${iconSrc} $out/share/icons/hicolor/scalable/apps/kakaotalk.svg

    # Crostini's garcon prefers PNG icons over the scalable fallback, and the
    # ChromeOS launcher and shelf look best with raster sizes available. Render
    # the same fetched SVG at build time instead of committing binaries.
    for size in 48 64 128 256; do
      mkdir -p $out/share/icons/hicolor/''${size}x''${size}/apps
      rsvg-convert --width "$size" --height "$size" \
        --output "$out/share/icons/hicolor/''${size}x''${size}/apps/kakaotalk.png" \
        ${iconSrc}
    done

    runHook postInstall
  '';

  meta = with lib; {
    description = "KakaoTalk messaging application, wrapped in Wine for Linux";
    homepage = "https://www.kakaocorp.com/page/service/service/KakaoTalk";
    license = licenses.unfree;
    platforms = [ "x86_64-linux" "i686-linux" ];
    mainProgram = "kakaotalk";
  };
}
