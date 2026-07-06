# Chromium OS Window Snap Algorithm

Source code references (chromium/src at `main`):

| File | URL |
|------|-----|
| `ash/wm/window_positioning_utils.cc` | https://github.com/chromium/chromium/blob/main/ash/wm/window_positioning_utils.cc |
| `ash/wm/window_positioning_utils.h` | https://github.com/chromium/chromium/blob/main/ash/wm/window_positioning_utils.h |
| `ash/wm/splitview/split_view_controller.cc` | https://github.com/chromium/chromium/blob/main/ash/wm/splitview/split_view_controller.cc |
| `ash/wm/splitview/split_view_utils.h` | https://github.com/chromium/chromium/blob/main/ash/wm/splitview/split_view_utils.h |
| `ash/wm/window_state.cc` | https://github.com/chromium/chromium/blob/main/ash/wm/window_state.cc |
| `chromeos/ui/base/chromeos_ui_constants.h` | https://github.com/chromium/chromium/blob/main/chromeos/ui/base/chromeos_ui_constants.h |
| `ash/wm/splitview/split_view_constants.h` | https://github.com/chromium/chromium/blob/main/ash/wm/splitview/split_view_constants.h |
| `ui/gfx/geometry/rect.h` | https://github.com/chromium/chromium/blob/main/ui/gfx/geometry/rect.h |

---

## 1. Core math: `GetSnappedWindowAxisLength`

File: [`ash/wm/window_positioning_utils.cc`](https://github.com/chromium/chromium/blob/main/ash/wm/window_positioning_utils.cc)

Takes: `snap_ratio`, `work_area_axis_length`, `min_axis_length`, `is_primary_snap`

### Primary snap (left / top)

```cpp
return std::clamp(static_cast<int>(snap_ratio * work_area_axis_length),
                  min_axis_length, work_area_axis_length);
```

**C++ truncation toward zero**, equivalent to `Math.floor` for positive values.
For default snap_ratio=0.5: `floor(W/2)`.

### Secondary snap (right / bottom)

```cpp
const int empty_space_axis_length =
    static_cast<int>((1 - snap_ratio) * work_area_axis_length);
return std::clamp(work_area_axis_length - empty_space_axis_length,
                  min_axis_length, work_area_axis_length);
```

**Purpose:** ensures primary + secondary widths sum exactly to `work_area_axis_length`
with no gaps from integer rounding. For 0.5:
- `empty = floor(0.5 * W)`
- `right_width = W - floor(0.5 * W) = ceil(W/2)`

### Right window placement

```cpp
case SnapRegion::kRight:
    snap_bounds.set_width(axis_length);
    snap_bounds.set_x(work_area.right() - axis_length);
    break;
```

**Right window left** = `wa.left + W - right_width` = `wa.left + W - ceil(W/2)` = `wa.left + floor(W/2)`

**Summary for default 0.5 ratio on a display of width W:**

| Position | Left (x) | Width |
|----------|----------|-------|
| Left snap | `wa.left` | `floor(W/2)` |
| Right snap | `wa.left + floor(W/2)` | `ceil(W/2)` |

For **even W** (e.g. 1920): both widths = 960, right starts at wa.left + 960.
For **odd W** (e.g. 1921): left width = 960, right width = 961, right starts at wa.left + 960.

---

## 2. `GetSnappedWindowBounds` (full function)

File: [`ash/wm/window_positioning_utils.cc`](https://github.com/chromium/chromium/blob/main/ash/wm/window_positioning_utils.cc)

### Input
- `work_area` — `gfx::Rect` of the target work area
- `display` — target display (for orientation detection)
- `window` — the window being snapped (for min size, preferred size)
- `type` — `SnapViewType::kPrimary` or `kSecondary`
- `snap_ratio` — float (default: `chromeos::kDefaultSnapRatio` = 0.5)

### Step 1: Determine snap region

Uses `GetSnapDisplayOrientation(display)` mapping:
- `kLandscapePrimary` → primary=left, secondary=right
- `kLandscapeSecondary` → primary=right, secondary=left
- `kPortraitPrimary` → primary=top, secondary=bottom
- `kPortraitSecondary` → primary=bottom, secondary=top

Physical position (left/top) = "primary" in the display's natural orientation.

### Step 2: Compute axis length

```
axis_length = GetSnappedWindowAxisLength(snap_ratio, work_area_axis_length,
                                          min_size, is_primary_snap)
```

Additionally checks:
- **Unresizable snapped window size** (`kUnresizableSnappedSizeKey`): override if set
- **Window restore**: if the window is being restored from a prior session, use the saved `snap_percentage`

### Step 3: Set bounds

Maps region → bounds:
- `kLeft` → `{x=wa.left, width=axis_length}` (y, height from work_area)
- `kRight` → `{x=wa.right-axis_length, width=axis_length}`
- `kTop` → `{y=wa.top, height=axis_length}`
- `kBottom` → `{y=wa.bottom-axis_length, height=axis_length}`

---

## 3. Orientation handling

File: [`ash/wm/window_positioning_utils.cc`](https://github.com/chromium/chromium/blob/main/ash/wm/window_positioning_utils.cc) — `GetSnapDisplayOrientation`

```cpp
chromeos::OrientationType GetSnapDisplayOrientation(const display::Display& display) {
    // clamshell mode only (tablet uses SplitViewController version)
    const auto& rotation = GetDisplayInfo(display.id()).GetActiveRotation();
    return RotationToOrientation(GetDisplayNaturalOrientation(display), rotation);
}
```

### `IsLayoutHorizontal`, `IsLayoutPrimary`

In [`ash/wm/splitview/split_view_utils.h`](https://github.com/chromium/chromium/blob/main/ash/wm/splitview/split_view_utils.h):

| Natural orientation | Rotation | `IsLayoutHorizontal` | `IsLayoutPrimary` |
|---------------------|----------|---------------------|-------------------|
| Landscape | 0° | true | true → left is primary |
| Landscape | 90° | false | false |
| Landscape | 180° | true | false → right is primary |
| Landscape | 270° | false | true |
| Portrait | 0° | false | true → top is primary |
| Portrait | 90° | true | false |
| Portrait | 180° | false | false |
| Portrait | 270° | true | true |

---

## 4. Snap ratios (fixed positions)

In [`chromeos/ui/base/chromeos_ui_constants.h`](https://github.com/chromium/chromium/blob/main/chromeos/ui/base/chromeos_ui_constants.h):

| Constant | Value | Name |
|----------|-------|------|
| `kDefaultSnapRatio` | `0.5f` | Half |
| `kOneThirdSnapRatio` | `0.3333f` | One third |
| `kTwoThirdSnapRatio` | `0.6667f` | Two thirds |

### Clamshell snap flow

1. [`WindowState::OnWMEvent(WindowSnapWMEvent)`](https://github.com/chromium/chromium/blob/main/ash/wm/window_state.cc) → `GetTargetSnapRatio()`
2. `WindowSnapWMEvent` carries `snap_ratio`, `snap_action_source`
3. `DefaultState` calls `SplitViewController::OnSnapEvent()` (if applicable)
4. `SnapWindow()` → `WindowState::OnWMEvent(WM_EVENT_SNAP_PRIMARY/SECONDARY)`
5. Window state transitions → `WindowState::SetBoundsDirectAnimated(snapped_bounds)`
6. `snap_ratio_` stored in `snap_ratio_` field of `WindowState`

### Tablet snap flow

Uses [`SplitViewController::GetSnappedWindowBoundsInScreen()`](https://github.com/chromium/chromium/blob/main/ash/wm/splitview/split_view_controller.cc) instead, which factors
in divider width and fixed position ratios (`kFixedPositionRatios`):
```cpp
constexpr std::array<float, 5> kFixedPositionRatios = {
    0.f, kOneThirdSnapRatio, kDefaultSnapRatio, kTwoThirdSnapRatio, 1.0f,
};
```

---

## 5. Minimum window size enforcement

In [`WindowState::GetTargetSnapRatio()`](https://github.com/chromium/chromium/blob/main/ash/wm/window_state.cc):

```cpp
float GetTargetSnapRatio(aura::Window* window, const WindowSnapWMEvent* snap_event) {
    // In clamshell mode, enforce min size:
    const float window_minimum_length = GetMinimumWindowLength(window, is_horizontal);
    const float snap_ratio = snap_event->snap_ratio();
    return std::max(window_minimum_length / work_area_axis_length, snap_ratio);
}
```

Also: [`AdjustBoundsToEnsureMinimumWindowVisibility()`](https://github.com/chromium/chromium/blob/main/ash/wm/window_positioning_utils.cc) enforces at least
`kMinimumOnScreenArea` (25 DIPs) or 30% of the window rect must be visible.

---

## 6. `isSnapPosition()` detection (for our extension)

Chromium OS determines snap position by checking if the window state type is
`kPrimarySnapped` or `kSecondarySnapped` (via [`WindowState::GetStateType()`](https://github.com/chromium/chromium/blob/main/ash/wm/window_state.cc)
comparing against [`WindowStateType`](https://github.com/chromium/chromium/blob/main/chromeos/ui/base/window_state_type.h)).
It does NOT check bounds geometry.

Our extension uses bounds-based detection because it has no access to `WindowState`.
The correct check (for landscape, default 0.5 ratio):

```typescript
function isSnapPosition(window, display) {
    const wa = display.workArea;
    const halfSnapWidth = Math.floor(wa.width / 2);
    const isLeft = window.left === wa.left && window.width === halfSnapWidth;
    const isRight = window.left === wa.left + Math.floor(wa.width / 2)
                 && window.width === wa.width - halfSnapWidth;
    //                                ^^^^^^^^^^^^^^^^^^^^^^^^^ = ceil(W/2)
    return isLeft || isRight;
}
```

Note: right window has width = `ceil(W/2)` (not `floor(W/2)`), because of how
`GetSnappedWindowAxisLength` computes secondary snap.

---

## 7. Asymmetric snap & odd widths (important)

For **odd screen width** (e.g. 1921px):

| | Left snap | Right snap |
|---|---|---|
| x | 0 | 960 |
| width | 960 | 961 |

The right window is **1px wider** because of C++ integer truncation.
No gap between windows (left+right = 1921 = full width).

This is verified by the Chromium test:
[`WindowPositioningUtilsTest.SnapBoundsWithOddNumberedScreenWidth`](https://github.com/chromium/chromium/blob/main/ash/wm/window_positioning_utils_unittest.cc)

---

## 8. Alt+[ / Alt+] shortcut handling

Chrome OS keyboard shortcuts for snap are handled by the **AcceleratorController**
([`accelerator_commands.cc`](https://github.com/chromium/chromium/blob/main/ash/accelerators/accelerator_commands.cc)). When pressed:

1. AcceleratorController detects Alt+[ or Alt+]
2. Creates `WindowSnapWMEvent` with `WM_EVENT_SNAP_PRIMARY` (Alt+[) or
   `WM_EVENT_SNAP_SECONDARY` (Alt+])
3. Sends to `WindowState::OnWMEvent()` for the focused window
4. WindowState transitions to `kPrimarySnapped` / `kSecondarySnapped`
5. `DefaultState::UpdateSnappedBounds()` calls `GetSnappedWindowBounds()`
6. Window bounds are set via `SetBoundsDirectAnimated(snapped_bounds)`
7. `SplitViewController::OnSnapEvent()` is invoked — in clamshell mode with
   overview active, this enters split-view mode

### Why the extension can't intercept Alt+[

Chrome OS's **AcceleratorController** processes keyboard shortcuts at the
**OS level**, before any Chrome extension's `chrome.commands.onCommand`
would fire. The extension sees the shortcut only if the OS doesn't claim it.
To use Alt+[ as an extension shortcut, the OS binding must first be changed
(or disabled) in `chrome://os-settings/keyboard`.
