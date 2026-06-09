# KakaoTalk Wine Wrapper for Nix / Nixpkgs

This package provides a standardized Nix expression for wrapping the official Windows version of KakaoTalk on Linux using Wine, suitable for upstream inclusion in `nixpkgs`.

## Technical Details

### 1. Isolated Wine Environment
To avoid conflicts with the default `~/.wine` prefix and other Wine applications, the launcher script sets up a dedicated, isolated prefix:
- **`WINEPREFIX`**: `~/.local/share/kakaotalk` (respects `XDG_DATA_HOME` if set)
- **`WINEARCH`**: `win64` (fully 64-bit architecture)

### 2. Korean Language & Locale Setup
To ensure Korean text rendering and correct encoding handling:
- **`LANG` & `LC_ALL`**: Set to `ko_KR.UTF-8` during execution.

### 3. Font Substitution and Mapping
By default, Wine struggles to render Korean characters (often displaying empty boxes or blocks) because it lacks standard Windows Korean fonts (like *Malgun Gothic*, *Gulim*, or *Dotum*).
To solve this offline:
- **Font Linking**: The launcher automatically scans the Nix store path of the `nanum` font package and symlinks all `*.ttf` files to `$WINEPREFIX/drive_c/windows/Fonts/`.
- **Registry Substitution**: On first run, a registry configuration is imported via `regedit` to map standard Windows fonts to their closest Nanum font counterparts:
  ```ini
  [HKEY_CURRENT_USER\Software\Wine\Fonts\Replacements]
  "MS Gothic"="NanumGothic"
  "MS PGothic"="NanumGothic"
  "MS Sans Serif"="NanumGothic"
  "Gulim"="NanumGothic"
  "GulimChe"="NanumGothic"
  "Batang"="NanumMyeongjo"
  "BatangChe"="NanumMyeongjo"
  "Dotum"="NanumGothic"
  "DotumChe"="NanumGothic"
  "맑은 고딕"="NanumGothic"
  "Malgun Gothic"="NanumGothic"
  ```
- **Application Preferences**: The script sets `CustomFontFaceName=NanumBarunGothic` inside KakaoTalk's `pref.ini` configurations.

### 4. Input Method Editor (IME) Support
To allow typing Korean inside KakaoTalk:
- The script automatically probes for running IME daemons (`fcitx`, `fcitx5`, `ibus`, `kime`, `nimf`).
- It configures the appropriate environmental variables (`XMODIFIERS`, `GTK_IM_MODULE`, `QT_IM_MODULE`) for the Wine process.

### 5. Automated Silent Installation
On first launch, the script checks if `KakaoTalk.exe` exists in the prefix. If not, it executes a silent installation:
```bash
wine KakaoTalk_Setup.exe /S
```
This performs a full, quiet installation of KakaoTalk into the prefix without requiring any user intervention, making the launch process feel native.

---

## Local Usage

### Building the Package
You can build the package locally using `nix-build` (pointing to the unstable channel to prevent warnings and resolve newer dependencies):
```bash
nix-build default.nix -I nixpkgs=https://channels.nixos.org/nixpkgs-unstable/nixexprs.tar.xz
```

Or using the monorepo's flake if it is added as a workspace/package input:
```bash
nix build .#kakaotalk
```

### Running the Application
Once built, run the application from the result symlink:
```bash
./result/bin/kakaotalk
```

The desktop shortcut and icon will automatically be installed to your desktop environment if you install this package via your system configuration (e.g., NixOS `environment.systemPackages` or Home Manager).

### Crostini / ChromeOS Desktop Integration
On ChromeOS (Crostini), the system launcher daemon (`garcon`) only indexes standard directories. To make KakaoTalk appear in your ChromeOS launcher drawer:

1. **Install the package to your user profile** (this adds `kakaotalk` to your `$PATH`):
   ```bash
   nix-env -f default.nix -iA kakaotalk
   ```

2. **Symlink the desktop entry and icon** to your user's local share directory:
   ```bash
   mkdir -p ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps
   ln -sf ~/.nix-profile/share/applications/kakaotalk.desktop ~/.local/share/applications/
   ln -sf ~/.nix-profile/share/icons/hicolor/scalable/apps/kakaotalk.svg ~/.local/share/icons/hicolor/scalable/apps/
   ```
   *Within a few seconds, the KakaoTalk icon will automatically appear in your ChromeOS search launcher.*

---

## References

1. **Official KakaoTalk**: [kakaocorp.com](https://www.kakaocorp.com/page/service/service/KakaoTalk)
2. **Arch Linux AUR Package (`ulagbulag/kakaotalk`)**: [GitHub Repository](https://github.com/ulagbulag/kakaotalk) - Provided the basis for Input Method Editor (IME) detection and the silent installation parameters.
3. **Nix Flake (`sodii/kakaotalk-nix`)**: [GitHub Repository](https://github.com/sodii/kakaotalk-nix) - Demonstrated registry font overrides and integration with Wikimedia SVG assets.
