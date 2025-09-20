// Define an interface for our storage options for type safety.
interface StorageOptions {
    avoidChromeOSSnap?: boolean;
}

// Your existing helper functions (no changes needed)
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
    // 1. Get user settings from storage. We default to 'true' to enable the fix.
    const options: StorageOptions = await chrome.storage.sync.get({
        avoidChromeOSSnap: true
    });

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

    // --- Standard Placement Logic ---
    if (isRightHalf) left += halfWidth;
    if (isBottomHalf) top += halfHeight;
    if (isLeftHalf || isRightHalf) width = halfWidth;
    if (isTopHalf || isBottomHalf) height = halfHeight;

    // --- ChromeOS Snap Fix Logic ---
    // If the option is enabled AND this is a left/right half window
    if (options.avoidChromeOSSnap && (isLeftHalf || isRightHalf)) {
        console.log("Applying ChromeOS snap fix...");
        // Use the "Overlap" method for maximum screen real estate.
        if (isLeftHalf) {
            // Make it 1px wider than half
            width = halfWidth + 1;
        }
        if (isRightHalf) {
            // Start it 1px to the left and make it 1px wider
            left = (display.workArea.left + halfWidth) - 1;
            width = halfWidth + 1;
        }
    }

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

chrome.runtime.onInstalled.addListener(details => {
    if (details.reason === "install") {
        chrome.storage.sync.set({ avoidChromeOSSnap: true });
        chrome.tabs.create({ url: "chrome://extensions/shortcuts" });
    }
});
