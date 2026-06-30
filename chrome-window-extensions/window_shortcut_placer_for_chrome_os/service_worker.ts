// Prevent re-entering our own snap-state-breaking update
const breakingSnap = new Set<number>();

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

async function place(positionNumber: number): Promise<chrome.windows.Window> {
    const focusedWindow = await chrome.windows.getLastFocused();
    const display = await getClosestDisplay(focusedWindow);

    let left = display.workArea.left;
    let top = display.workArea.top;
    let width = display.workArea.width;
    let height = display.workArea.height;

    const halfWidth = display.workArea.width / 2;
    const halfHeight = display.workArea.height / 2;

    const isLeftHalf = [1, 4, 7].includes(positionNumber);
    const isRightHalf = [3, 6, 9].includes(positionNumber);
    const isTopHalf = [7, 8, 9].includes(positionNumber);
    const isBottomHalf = [1, 2, 3].includes(positionNumber);

    if (isRightHalf) left += halfWidth;
    if (isBottomHalf) top += halfHeight;
    if (isLeftHalf || isRightHalf) width = halfWidth;
    if (isTopHalf || isBottomHalf) height = halfHeight;

    const placingBounds: chrome.windows.UpdateInfo = {
        top: Math.round(top),
        left: Math.round(left),
        width: Math.round(width),
        height: Math.round(height),
        state: "normal",
    };

    return chrome.windows.update(focusedWindow.id, placingBounds);
}


chrome.commands.onCommand.addListener(command => {
    const position = parseInt(command.slice(-1));
    if (!isNaN(position)) {
        place(position);
    }
});

// Break ChromeOS snap state whenever a window enters it.
// ChromeOS Snap Groups form only when windows are in snapped state.
// By breaking the state immediately, we prevent Snap Groups from forming,
// which avoids the bug where closing a snap partner restores the other
// window to full size.
chrome.windows.onBoundsChanged.addListener(async (windowId: number) => {
    if (breakingSnap.has(windowId)) return;

    try {
        const win = await chrome.windows.get(windowId);
        if (!win || win.state !== "snapped") return;

        breakingSnap.add(windowId);
        await chrome.windows.update(windowId, {
            left: win.left,
            top: win.top,
            width: win.width,
            height: win.height,
            state: "normal",
        });
    } catch {
        // Window may have closed during async operations
    } finally {
        breakingSnap.delete(windowId);
    }
});

chrome.runtime.onInstalled.addListener(details => {
    if (details.reason === "install") {
        chrome.tabs.create({ url: "chrome://extensions/shortcuts" });
    }
});
