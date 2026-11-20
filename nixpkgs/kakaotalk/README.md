# KakaoTalk Wine Wrapper for Nix / Nixpkgs

This package provides a standardized Nix expression for wrapping the official Windows version of KakaoTalk on Linux using Wine, suitable for upstream inclusion in `nixpkgs`.

The first launch downloads the latest installer from Kakao over HTTPS; internet
access is required. Installed prefixes skip the download. The package version
tracks the launcher, not the downloaded application.

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

### 6. Wine System Tray Window
Wine's `explorer.exe` implements the Windows notification area as its own
top-level window (class `Shell_TrayWnd`) whenever an application registers a
tray icon. On ChromeOS there is no Win32/XEmbed tray host, so that window is
exported to the Wayland compositor like any other window. It has no title, is
about 166x52 pixels, and belongs to `explorer.exe`, whose `app_id` has no
matching desktop entry, so it appears as a stray window with a generic icon.

The launcher sets `HKCU\Software\Wine\Explorer\ShowSystray` (`REG_DWORD 0`), so
Wine keeps the tray icon registrations but does not display the standalone tray
window. Because `explorer.exe` reads the value only at startup, the launcher
also restarts `explorer.exe` once when the persisted value is missing, which
covers existing prefixes and the explorer that Wine may spawn while the
registry write is still in flight.

Trade-off: there is no visible tray icon. Closing the main window with its X
button quits KakaoTalk, and closing it to the tray (if enabled in KakaoTalk's
settings) leaves it running but hidden. In both cases launching `kakaotalk`
again restores the main window: a new instance is started, or the single
running instance shows its hidden window again.

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

2. **Symlink the desktop entry and install the icons** into your user's local share directories:
   ```bash
   mkdir -p ~/.local/share/applications ~/.local/share/icons/hicolor
   ln -sf ~/.nix-profile/share/applications/kakaotalk.exe.desktop ~/.local/share/applications/
   cp -rL ~/.nix-profile/share/icons/hicolor/. ~/.local/share/icons/hicolor/
   ```
   *Within a few seconds, the KakaoTalk icon will automatically appear in your ChromeOS search launcher.*

The desktop file is deliberately named `kakaotalk.exe.desktop`. ChromeOS matches a
native Wayland window to a launcher by comparing the window's `app_id` with the
desktop file ID; `StartupWMClass` is only used for X11/Xwayland windows. Wine's
Wayland driver derives the `app_id` from the executable name, so KakaoTalk
reports `kakaotalk.exe`. The matching desktop file ID is therefore
`kakaotalk.exe`, and `StartupWMClass=kakaotalk.exe` keeps X11 matching intact.

The package also renders its fetched SVG logo into PNG icons
(`48x48`–`256x256`). Garcon, which exports apps and icons to ChromeOS, prefers
PNG over the scalable SVG fallback.

---

## References

1. **Official KakaoTalk**: [kakaocorp.com](https://www.kakaocorp.com/page/service/service/KakaoTalk)
2. **Arch Linux AUR Package (`ulagbulag/kakaotalk`)**: [GitHub Repository](https://github.com/ulagbulag/kakaotalk) - Provided the basis for Input Method Editor (IME) detection and the silent installation parameters.
3. **Nix Flake (`sodii/kakaotalk-nix`)**: [GitHub Repository](https://github.com/sodii/kakaotalk-nix) - Demonstrated registry font overrides and integration with Wikimedia SVG assets.

---

## ChromeOS / Sommelier-RS / Wayland Native Driver Integration

For high-fidelity input (including Korean IME) on ChromeOS, KakaoTalk must run with Wine's native Wayland graphics driver instead of Xwayland. This routes input through the custom `sommelier-rs` Wayland proxy running on `wayland-1`.

### 1. Wine Native Wayland Graphics Driver
By default, Wine fallbacks to X11. The wrapper forces Wayland by:
- Setting the Wine registry key `HKCU\Software\Wine\Drivers\Graphics = "wayland"`.
- Clearing the `DISPLAY` environment variable when `WAYLAND_DISPLAY="wayland-1"` is set.

### 2. Sommelier-RS Protocol Stability Fixes
During early development, Wine's native Wayland driver crashed with the following error:
```
wl_display#1: error 1: invalid arguments for zwp_text_input_v1#12.activate
```
This was caused by a protocol mismatch when bridging Wayland's `zwp_text_input_v3` (client-side) to `zwp_text_input_v1` (host-side):
1. **Unmapped Object Guards**: `sommelier-rs` attempted to forward `activate` and `deactivate` requests to the host compositor before the target surface focus or seat was correctly mapped. In the Wayland wire protocol, sending `0` for non-nullable object parameters (like `wl_seat` and `wl_surface`) is a fatal protocol error.
2. **Focus Handling**: We added guards to `TextInputV3Handler::on_commit` to only forward `activate` requests when `host_seat != 0 && host_surface != 0`, and `deactivate` requests when `host_seat != 0`.
3. **Reverting Redundant Events**: Emulated `on_enter` and `on_leave` forward handlers in the v1 text-input bridge were removed. Focus state is already fully managed by `KeyboardHandler`, avoiding desynchronized state and protocol assertion failures.

### 3. Local Development & Deployment Workflow
To make changes to `sommelier-rs` and test them in KakaoTalk (assuming the `sommelier-rs` and `monorepo-public` repositories are checked out side-by-side):

1. **Build the Custom Proxy**:
   ```bash
   cd sommelier-rs
   cargo build
   ```
2. **Stage the Binary**:
   Copy the debug binary to the development setup directory inside `monorepo-public`:
   ```bash
   cp target/debug/sommelier ../monorepo-public/chromeos-dev-setup/sommelier-rs.local.bin
   ```
3. **Deploy & Restart Service**:
   Run the dev setup script from the root of the `monorepo-public` repository to rebuild the local derivation and restart the systemd service:
   ```bash
   cd ../monorepo-public
   ./chromeos-dev-setup/setup.bash
   ```
4. **Launch KakaoTalk**:
   Start KakaoTalk on the secondary Wayland display:
   ```bash
   WAYLAND_DISPLAY=wayland-1 kakaotalk
   ```
   Check the `sommelier-rs` logs via:

   ```bash
   journalctl --user -u sommelier-rs -f
   ```
