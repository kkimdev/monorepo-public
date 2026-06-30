interface StorageOptions {
    avoidChromeOSSnap?: boolean;
}

interface SnapBounds {
    left: number;
    top: number;
    width: number;
    height: number;
}

const snapAtBounds = new Map<number, SnapBounds>();
const pendingRestore = new Set<number>();

interface Point {
    x: number;
    y: number;
}

function computeCenter(box: chrome.system.display.Bounds | chrome.windows.Window): Point {
    return {
        x: box.left + box.width / 2,
        y: box.top + box.height / 2
    };
}

function computeDistance(point1: Point, point2: Point): number {
    return (point2.x - point1.x) ** 2 + (point2.y - point1.y) ** 2;
}

async function getClosestDisplay(window: chrome.windows.Window): Promise<chrome.system.display.DisplayInfo> {
    const displayInfos = await chrome.system.display.getInfo();
    if (displayInfos.length === 1) {
        return displayInfos[0];
    }

    const windowCenter = computeCenter(window);
    let minDistance = Infinity;
    let closestDisplay = displayInfos[0];

    for (const displayInfo of displayInfos) {
        const distance = computeDistance(windowCenter, computeCenter(displayInfo.workArea));
        if (distance < minDistance) {
            minDistance = distance;
            closestDisplay = displayInfo;
        }
    }
    return closestDisplay;
}

function isSnapPosition(window: chrome.windows.Window, display: chrome.system.display.DisplayInfo): boolean {
    const wa = display.workArea;
    const halfSnapWidth = Math.floor(wa.width / 2);
    const isLeft = window.left === wa.left && window.width === halfSnapWidth;
    const isRight = window.left === wa.left + wa.width - halfSnapWidth && window.width === halfSnapWidth;
    return isLeft || isRight;
}

async function place(positionNumber: number): Promise<chrome.windows.Window> {
    const focusedWindow = await chrome.windows.getLastFocused();
    const display = await getClosestDisplay(focusedWindow);

    const wa = display.workArea;
    let left = wa.left;
    let top = wa.top;
    let width = wa.width;
    let height = wa.height;

    // ChromeOS GetSnappedWindowAxisLength() truncates: static_cast<int>(axis_length / 2)
    const halfSnapWidth = Math.floor(wa.width / 2);
    const halfSnapHeight = Math.floor(wa.height / 2);

    const isLeftHalf = [1, 4, 7].includes(positionNumber);
    const isRightHalf = [3, 6, 9].includes(positionNumber);
    const isTopHalf = [7, 8, 9].includes(positionNumber);
    const isBottomHalf = [1, 2, 3].includes(positionNumber);

    if (isRightHalf) left += wa.width - halfSnapWidth;
    if (isBottomHalf) top += halfSnapHeight;
    if (isLeftHalf || isRightHalf) width = halfSnapWidth;
    if (isTopHalf || isBottomHalf) height = halfSnapHeight;

    const placingBounds: chrome.windows.UpdateInfo = {
        top: Math.round(top),
        left: Math.round(left),
        width: Math.round(width),
        height: Math.round(height),
        state: "normal",
    };

    console.log("Placing window", focusedWindow.id, "to", placingBounds);
    return chrome.windows.update(focusedWindow.id, placingBounds);
}


chrome.commands.onCommand.addListener(command => {
    const position = parseInt(command.slice(-1));
    if (!isNaN(position)) {
        console.log('Command received:', command);
        place(position);
    }
});

// Detect Snap Groups restore: when a snapped window's partner closes,
// ChromeOS restores the remaining window to its pre-snap bounds.
// We detect this by tracking windows at snap positions and checking
// if a bounds change immediately follows a window close.
chrome.windows.onBoundsChanged.addListener(async (window: chrome.windows.Window) => {
    const options: StorageOptions = await chrome.storage.sync.get({
        avoidChromeOSSnap: true
    });
    if (!options.avoidChromeOSSnap) return;

    // If a window closure was detected, check if this window needs re-snapping
    if (pendingRestore.has(window.id)) {
        pendingRestore.delete(window.id);
        const snapInfo = snapAtBounds.get(window.id);
        if (snapInfo) {
            console.log("Snap restore detected, re-snapping window", window.id);
            await chrome.windows.update(window.id, {
                left: snapInfo.left,
                top: snapInfo.top,
                width: snapInfo.width,
                height: snapInfo.height,
                state: "normal",
            });
            return;
        }
    }

    // Track windows at snap positions; clear stale entries when moved away
    const display = await getClosestDisplay(window);
    if (isSnapPosition(window, display)) {
        snapAtBounds.set(window.id, {
            left: window.left,
            top: window.top,
            width: window.width,
            height: window.height,
        });
    } else {
        snapAtBounds.delete(window.id);
    }
});

// When a snapped window closes, mark remaining snapped windows as potentially needing restore.
// Non-snapped window closures are ignored to avoid false positives.
chrome.windows.onRemoved.addListener(windowId => {
    const wasSnapped = snapAtBounds.has(windowId);
    snapAtBounds.delete(windowId);
    if (wasSnapped) {
        for (const snappedId of snapAtBounds.keys()) {
            pendingRestore.add(snappedId);
            setTimeout(() => pendingRestore.delete(snappedId), 500);
        }
    }
});

chrome.runtime.onInstalled.addListener(details => {
    if (details.reason === "install") {
        chrome.storage.sync.set({ avoidChromeOSSnap: true });
        chrome.tabs.create({ url: "chrome://extensions/shortcuts" });
    }
});
