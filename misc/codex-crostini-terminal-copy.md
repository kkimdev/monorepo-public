# Codex drag-to-copy failure in Orca on Crostini

Investigation date: 2026-10-06

## Findings

Codex's Fullscreen mode handles ordinary mouse drags itself, bypassing Orca's terminal selection and copy handling. In this environment, Codex writes directly to the X11 clipboard and reports success, but pasting in ChromeOS returns the previous clipboard contents.

**Shift+drag worked in practice.** It uses Orca's own terminal selection and copy handling. This confirms that copying from Orca to ChromeOS works; Codex's choice of clipboard backend is the key difference that exposes the problem.

The specific reason the X11 copy does not reach ChromeOS remains unconfirmed. Source inspection and runtime evidence establish that Codex's success message does not guarantee that the ChromeOS clipboard was updated.

## Symptoms and environment

- Orca runs on Debian Linux inside ChromeOS Crostini.
- The running versions were Orca `1.4.217` and Codex CLI `0.159.1`.
- The Codex session had `TERM_PROGRAM=Orca`, `TERM=xterm-256color`, `DISPLAY=:1`, and `WAYLAND_DISPLAY=wayland-2`.
- The session was local, without SSH or tmux.
- A running custom `sommelier-rs 0.2.5` provided `wayland-2`.
- After an ordinary drag, Codex displayed `Copied … chars to host clipboard` in the lower-right corner.
- Pasting into both the Codex input in Orca and other ChromeOS apps returned the previous clipboard contents.
- The user observed that the problem started around a recent Codex update, when this notification first appeared.

## Verified evidence

### 1. Codex displays the success message

Codex `0.159.1` contains the exact screenshot text in [`transcript_view/composer_gap.rs`](https://github.com/openai/codex/blob/rust-v0.159.1/codex-rs/tui/src/transcript_view/composer_gap.rs). It displays `Copied {characters} chars to host clipboard` for `CopyStatus::Confirmed`.

This status means Codex considers the direct clipboard write successful. It does not verify that the text can be pasted in ChromeOS.

### 2. The current Codex process owned the newly copied data

After the user performed another drag-to-copy during the investigation, the X11 `CLIPBOARD` selection on display `:1` was inspected.

- It contained 1,126 characters. The clipboard text is omitted from this document.
- Offered types included `UTF8_STRING`, `text/plain`, and `text/html`.
- An ownership query through the X11 `X-Resource` extension identified the Codex process running this task.
- The owner PID was `3788743` at the time of inspection. This is a historical value and changes after a restart.

This is runtime evidence that Codex directly owns and serves the X11 clipboard data.

### 3. Local sessions do not send OSC 52 after a successful native copy

[`clipboard_copy.rs`](https://github.com/openai/codex/blob/rust-v0.159.1/codex-rs/tui/src/clipboard_copy.rs) implements the following behavior:

1. Attempt a native OS clipboard copy through `arboard`.
2. In SSH or tmux, also attempt terminal forwarding regardless of native copy success.
3. In an ordinary local session, use OSC 52 as a fallback when the native copy fails.
4. Treat a successful native copy as `CopyStatus::Confirmed`.

The inspected session was local, and both the native success notification and X11 ownership were verified. The observed copy therefore used the native backend rather than the OSC 52 fallback.

The raw OSC 52 stream was not captured. Orca CLI's `terminal read` strips escape sequences, so that API could not reconstruct a past copy request's raw sequence.

### 4. Missing Wayland data-control leads to the X11 backend

Codex `0.159.1` uses `arboard 3.6.1` with the `wayland-data-control` feature. The inspected `wayland-2` registry advertised `wl_data_device_manager` but no Wayland data-control protocol.

Falling back to X11 when Wayland data-control is unavailable is consistent with the backend's source behavior. Runtime inspection also confirmed that Codex owned the copied X11 data.

### 5. Orca's own copy handling works

The running Orca installation's bundled source and active profile settings were inspected without modifying them.

| Setting | Observed value |
| --- | --- |
| `terminalAllowOsc52Clipboard` | `true` |
| `terminalClipboardOnSelect` | `true` |

On Linux, xterm treats a drag with Shift held as terminal selection and does not forward those mouse events to Codex. Orca copies the text when the selection changes.

The user confirmed **successful pasting after Shift+drag**. The inspected setting also confirms that OSC 52 was enabled. Orca's copy handling is therefore not universally broken, although Shift+drag success does not validate OSC 52 forwarding itself.

Orca handles incoming OSC 52 requests as follows:

```text
OSC 52 received → xterm parser → queueMicrotask
→ writeTerminalClipboardText → Electron IPC
→ clipboard.writeText → immediate comparison with clipboard.readText
```

This immediate readback check does not verify pasting into a ChromeOS app either.

## Recent Codex changes and related reports

| Link | Relevance |
| --- | --- |
| [PR #47639](https://github.com/openai/codex/pull/47639) | Merged 2026-09-23. Added `auto`, `always`, and `never` modes for `tui.copy_on_select`, with copying on mouse release |
| [PR #48469](https://github.com/openai/codex/pull/48469) | Merged 2026-09-26. Enabled automatic copying on selection release in additional terminals, including unknown terminals |
| [Issue #49192](https://github.com/openai/codex/issues/49192) | Crostini report: copy confirmation appears, but the clipboard is not updated |
| [Issue #49530](https://github.com/openai/codex/issues/49530) | Selection and copy failures after an update in the default ChromeOS terminal |
| [Issue #34160](https://github.com/openai/codex/issues/34160) | Similar backend selection problem: a successful native write to a container's X11 clipboard suppresses the OSC 52 fallback that would reach the host |
| [Issue #48664](https://github.com/openai/codex/issues/48664) | Request to disable mouse capture independently while retaining Fullscreen mode |

The inspected configuration had no explicit `copy_on_select` override. The default change in [`local_settings.rs`](https://github.com/openai/codex/blob/rust-v0.159.1/codex-rs/tui/src/local_settings.rs) is consistent with the timing and behavior change reported by the user.

These reports describe similar cases; they do not independently establish the specific cause in this environment. Their status was assessed at the investigation date.

## Ranked solutions

The ranking considers restoring ordinary drag-to-copy, retaining the Fullscreen UI, side effects, and maintenance cost. None of the proposed changes below has been implemented.

| Rank | Solution | Benefits | Costs and limitations |
| --- | --- | --- | --- |
| 1 | Add an explicit clipboard backend setting to Codex and select OSC 52 | Retains Fullscreen, mouse selection, and Markdown copying while delegating the actual clipboard write to Orca | No such setting currently exists; requires a Codex change and distribution. OSC 52 delivery in this environment still needs validation |
| 2 | Restart in Scrollback mode | Uses a supported setting to return selection to the terminal, with little maintenance | Gives up Fullscreen and some mouse features. Successful copying after restart has not been verified here |
| 3 | Unset `DISPLAY` and `WAYLAND_DISPLAY` at launch to trigger OSC 52 fallback | Allows an experiment that retains Fullscreen without modifying Codex | Affects native image clipboard access and GUI tools launched by Codex. Behavior remains unverified |
| 4 | Add an Orca option to force terminal selection | Could use the verified terminal copy handling without holding Shift | Conflicts with Codex's mouse interactions and requires an Orca change |
| 5 | Fix the cause of failed X11-to-ChromeOS clipboard forwarding | Could benefit other X11 applications | Requires further diagnosis and has the broadest scope |

The immediately available workaround verified in practice is **Shift+drag**. For a lasting solution that retains Fullscreen, option 1 is preferred: make the clipboard backend explicitly selectable. A configurable choice is easier to maintain than hardcoding terminal names.

Setting only `tui.copy_on_select="never"` does not restore ordinary terminal selection. It disables automatic copying, but Codex can continue capturing mouse events, preventing Orca from handling the drag.

## Applying `/tui` changes

`/tui` saves the mode for **the next launch**. It does not immediately switch the current session.

After the user selected Scrollback, the following message was observed on screen:

```text
Saved TUI mode: Scrollback. Restart Codex to apply; launch overrides still apply.
```

The configuration saved `tui.fullscreen_transcript=false`, but the running session was still in Fullscreen mode. Both [`tui_mode_picker.rs`](https://github.com/openai/codex/blob/rust-v0.159.1/codex-rs/tui/src/chatwidget/tui_mode_picker.rs) and the [configuration persistence code](https://github.com/openai/codex/blob/rust-v0.159.1/codex-rs/tui/src/app/tui_mode_picker.rs) explicitly state that a restart is required.

To apply the saved preference, exit with `/quit` and resume from the same working directory:

```sh
codex resume --last
```

Ordinary drag-to-copy after restarting has not yet been verified. Mode overrides in the launch command take precedence over the saved preference.

## Validation status and next steps

| Check | Status |
| --- | --- |
| Ordinary drag reports success, but pasting returns previous contents | Confirmed by the user |
| Pasting succeeds after Shift+drag | Confirmed by the user |
| The current Codex process owns the native X11 copy | Verified at runtime |
| A successful local native copy skips OSC 52 | Verified in source pinned to the installed version |
| Orca allows OSC 52 and copies terminal selections | Settings verified in the active profile |
| Scrollback preference is saved and requires a restart | Verified on screen, in configuration, and in source |
| Ordinary drag-to-copy after restarting in Scrollback | Unverified |
| Pasting in ChromeOS after forcing OSC 52 forwarding | Unverified |
| Specific cause of failed X11-to-ChromeOS forwarding | Unconfirmed |

The next check is to restart in Scrollback mode and verify ordinary drag-to-copy. If Fullscreen must be retained, validate OSC 52 delivery in a separate session before defining the scope of a clipboard backend setting.

The investigation did not change packages or system configuration, restart the app, or restart services. The user saved the Scrollback preference directly through `/tui`.
