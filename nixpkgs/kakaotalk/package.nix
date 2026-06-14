{ lib
, stdenv
, fetchurl
, makeDesktopItem
, copyDesktopItems
, replaceVars
, wineWow64Packages
, nanum
, bash
}:

let
  installerSrc = fetchurl {
    url = "https://lk.kakaocdn.net/talkpc/talk/win32/x64/KakaoTalk_Setup.exe";
    hash = "sha256-23VxEYW7r1ngvea+OwK8002UPf69DvBpEzUvVB8yOSw=";
  };

  iconSrc = fetchurl {
    url = "https://upload.wikimedia.org/wikipedia/commons/e/e3/KakaoTalk_logo.svg";
    hash = "sha256-0N1j/BFQCtpUN3mnO8PRMpB5Xaf3Ozh7bqX+ME8kTZk=";
  };

  # Use wineWow64Packages.stable by default if available, otherwise fallback to wine
  wine = wineWow64Packages.stable;

  runner = replaceVars ./kakaotalk-runner {
    inherit wine nanum bash;
    installer = installerSrc;
  };
in
stdenv.mkDerivation rec {
  pname = "kakaotalk-bin";
  version = "3.7.0"; # Align with the current installer version major/minor

  src = installerSrc;
  dontUnpack = true;

  nativeBuildInputs = [ copyDesktopItems ];

  desktopItems = [
    (makeDesktopItem {
      name = "kakaotalk";
      exec = "kakaotalk %U";
      icon = "kakaotalk";
      desktopName = "KakaoTalk";
      genericName = "Messenger";
      comment = "Where people and the world come to get connected";
      categories = [ "Network" "InstantMessaging" ];
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
