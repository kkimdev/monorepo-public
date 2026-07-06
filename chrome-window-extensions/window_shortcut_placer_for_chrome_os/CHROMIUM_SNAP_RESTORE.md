# Chrome OS Snap Restore Mechanism

When Chrome OS snaps a window and the snap is broken (window returns to "normal" state), the window is restored to its pre-snap position and size. This document describes the mechanism.

## Source Files

- **WindowState** — `https://github.com/chromium/chromium/blob/main/ash/wm/window_state.cc`
  - `SaveCurrentBoundsForRestore()` — saves pre-snap bounds to `kRestoreBoundsKey` aura property (line ~740)
  - `Restore()` — sends `WM_EVENT_NORMAL` event to transition from snapped → normal (line ~248)
  - `GetRestoreBoundsInScreen()` — reads saved pre-snap bounds from property (line ~770)
  - `SetRestoreBoundsInScreen()` — writes pre-snap bounds to `kRestoreBoundsKey` (line ~775)
  - `ClearRestoreBounds()` — removes the restore property (line ~780)

- **DefaultState** — `https://github.com/chromium/chromium/blob/main/ash/wm/default_state.cc`
  - Handles `WM_EVENT_NORMAL` for snapped windows by applying restore bounds
  - `UpdateWindowToNormalState()` — transitions from snapped to normal, reads restore bounds, applies them

- **WindowState.h** — `https://github.com/chromium/chromium/blob/main/ash/wm/window_state.h`
  - `kRestoreBoundsKey` is `aura::client::kRestoreBoundsKey`

- **SplitViewController** — `https://github.com/chromium/chromium/blob/main/ash/wm/splitview/split_view_controller.cc`
  - `OnWindowStateChanged()` — detects snapped→normal transition and ends split view (line ~1600)
  - Comment: "A left clamshell split view window gets unsnapped by Alt+["

- **AcceleratorController** — `https://github.com/chromium/chromium/blob/main/ash/accelerators/accelerator_controller.cc`
  - Handles Alt+[/] accelerators for window snapping at OS level

## Restore Flow

```
User presses Alt+[
  ├─ Chrome OS AcceleratorController handles Alt+[
  │   └─ WindowSnapWMEvent(WM_EVENT_SNAP_PRIMARY) → WindowState::OnWMEvent
  │       └─ SnapController snaps window to left half
  │           └─ WindowState::SaveCurrentBoundsForRestore()
  │               └─ aura::client::kRestoreBoundsKey = pre-snap geometry
  │
  ├─ onBoundsChanged fires (snapped position)
  │
  ├─ Extension auto-places: chrome.windows.update({state:"normal", left, top, width, height})
  │   └─ Chrome OS processes state change: snapped → normal
  │       └─ DefaultState::OnWMEvent(WM_EVENT_NORMAL)
  │           └─ WindowState::Restore()
  │               └─ Reads kRestoreBoundsKey → applies pre-snap bounds
  │
  └─ onBoundsChanged fires (restored pre-snap position)
```

## Key Insight

Setting `state: "normal"` on a snapped window triggers the restore mechanism. The restore applies `kRestoreBoundsKey` (pre-snap geometry), OVERWRITING any bounds set in the same `chrome.windows.update` call.

This means **any programmatic `state: "normal"` on a snapped window reverts it to pre-snap geometry**.

## Implications for Window Shortcut Placer

The extension's auto-place (triggered when Chrome OS snaps a window) calls `chrome.windows.update` with `state: "normal"` to exit the snap layout. This triggers the restore. The extension must then fight-back in `onBoundsChanged` to re-apply its grid position.

This requires a two-cycle approach:
1. Auto-place: change to `state: "normal"` (triggers restore) → window goes to pre-snap position
2. Fight-back: `onBoundsChanged` fires → re-apply grid position → window goes to grid position

The fight-back must NOT require the window to be at a snap position, because the restored position is the pre-snap geometry (typically not a snap).
