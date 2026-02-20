# Keyboard Navigator

A lightweight browser extension for keyboard-driven web navigation.

## Features
- **Hint Overlays**: Tap `Shift` to show hints for interactive elements.
- **Same-Tab Navigation**: Tap `Left Shift` to activate and open links in the current tab.
- **Background-Tab Navigation**: Tap `Right Shift` to activate and open links in a new background tab (simulates `Ctrl+Click`).
- **Interactive Filtering**: Type the letters shown on hints to select elements.
- **Smart Labeling**: Labels are optimized for typing ergonomics (home row first).

## Build Instructions

This project uses [Nix](https://nixos.org/) for building the extension.

### Build the extension
To build the extension into a zip file or a loadable directory, run:
```bash
nix build
```

The resulting build will be available in the `result/` directory.

Upload to https://chrome.google.com/webstore/devconsole

## Development
To load the extension into Chrome:
1. Run `nix build`.
2. Go to `chrome://extensions/`.
3. Enable "Developer mode".
4. Click "Load unpacked" and select the folder pointed to by the `result` symlink.
