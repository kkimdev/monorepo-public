// TODO: console.debug is not working for some reason so using console.log for now.

interface Point {
    x: number;
    y: number;
}

let displayChangedVersion: number = 0;
let scaledWindowBounds = {}
let chromeSystemDisplayGetInfoCache = undefined;

function logger(...msgs) {
    // TODO: console.debug is not working for some reason so using console.log for now.
    console.log('\t'.repeat(Error().stack.split('\n').length - 2), ...msgs);
}

function computeCenter(box): Point {
    return {
        x: box.left + box.width / 2,
        y: box.top + box.height / 2
    };
}

function computeDistance(point1: Point, point2: Point): number {
    return (point2.x - point1.x) ** 2 + (point2.y - point1.y) ** 2;
}

function getClosestDisplay(window) {
    if (chromeSystemDisplayGetInfoCache.length === 1)
        return chromeSystemDisplayGetInfoCache[0];

    const windowCenter = computeCenter(window);
    // logger(displayInfos);
    // logger('windowCenter', windowCenter);

    // TODO: minimum distance logic might not be the best algorithm.
    let minDistance = Infinity;
    let minDistanceWindow = undefined

    for (const displayInfo of chromeSystemDisplayGetInfoCache) {
        const distance = computeDistance(windowCenter, computeCenter(displayInfo.workArea));
        // logger('displayCenter', computeCenter(displayInfo.workArea));
        // logger('distance', distance);
        if (distance < minDistance) {
            minDistance = distance;
            minDistanceWindow = displayInfo;
        }
    }

    return minDistanceWindow;
}

async function addAllWindows(): Promise<void> {
    logger("addAllWindows");
    const allWindows = await chrome.windows.getAll();
    chromeSystemDisplayGetInfoCache = await chrome.system.display.getInfo();
    for (const window of allWindows) {
        addWindow(window);
    }
}

function addWindow(window: chrome.windows.Window): void {
    const closestDisplay = getClosestDisplay(window);
    const newScaledWindowBound = {
        displayChangedVersion: displayChangedVersion,
        left: window.left / closestDisplay.workArea.width,
        width: window.width / closestDisplay.workArea.width,
        top: window.top / closestDisplay.workArea.height,
        height: window.height / closestDisplay.workArea.height,
    };

    logger("addWindow", window, "from", scaledWindowBounds[window.id], "to", newScaledWindowBound);
    scaledWindowBounds[window.id] = newScaledWindowBound;
}

function updateWindow(window: chrome.windows.Window): void {
    logger("updateWindow", window);
    if (scaledWindowBounds[window.id]['displayChangedVersion'] < displayChangedVersion) {
        logger("updateWindow ignored because displayChangedVersion is lower", scaledWindowBounds[window.id]['displayChangedVersion'], "<", displayChangedVersion);
        return;
    }

    addWindow(window);
}

function removeWindow(windowId: number) {
    delete scaledWindowBounds[windowId];
}

async function repositionWindows(version: number) {
    logger("repositionWindows start, version:", version);
    chromeSystemDisplayGetInfoCache = await chrome.system.display.getInfo();

    for (const [windowId, value] of Object.entries(scaledWindowBounds)) {
        const scaledBound = scaledWindowBounds[windowId];
        const window = await chrome.windows.get(parseInt(windowId));
        const closestDisplay = getClosestDisplay(window);

        if (version < displayChangedVersion)
            break;

        console.assert(scaledBound['displayChangedVersion'] < displayChangedVersion)

        const newBound = {
            left: Math.round(scaledBound.left * closestDisplay.workArea.width),
            width: Math.round(scaledBound.width * closestDisplay.workArea.width),
            top: Math.round(scaledBound.top * closestDisplay.workArea.height),
            height: Math.round(scaledBound.height * closestDisplay.workArea.height),
            state: "normal"
        } as const;

        logger("repositionWindows update window:", window, "from", scaledBound, "to", newBound);
        chrome.windows.update(window.id, newBound);
        addWindow(window);
    }
    logger("repositionWindows end, version:", version);
}

function addListeners() {
    chrome.system.display.onDisplayChanged.addListener(
        () => {
            logger("chrome.system.display.onDisplayChanged");
            displayChangedVersion += 1;
            repositionWindows(displayChangedVersion);
        }
    );

    chrome.windows.onBoundsChanged.addListener(
        (window) => {
            logger("chrome.windows.onBoundsChanged", window);
            updateWindow(window);
        }
    );

    chrome.windows.onCreated.addListener(
        (window) => {
            logger("chrome.windows.onCreated", window);
            addWindow(window);
        }
    );

    chrome.windows.onRemoved.addListener(
        (windowId) => {
            logger("chrome.windows.onRemoved", windowId);
            removeWindow(windowId);
        }
    );
}

addListeners();
// TODO: Need to ensure `addAllWindows()` is finished before listener handling.
//       Though it will be the case 99.99% times in practice already.
addAllWindows();

let s = chrome.storage.session.get();
console.log(s.getBytesInUse());