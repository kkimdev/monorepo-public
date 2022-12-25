// TODO: console.debug is not working for some reason so using console.log for now.

interface Point {
    x: number;
    y: number;
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

async function getClosestDisplay(window: chrome.windows.Window): Promise<chrome.system.display.DisplayInfo> {
    const displayInfos = await chrome.system.display.getInfo();
    if (displayInfos.length === 1)
        return displayInfos[0];

    const windowCenter = computeCenter(window);
    // logger(displayInfos);
    // logger('windowCenter', windowCenter);

    // TODO: minimum distance logic might not be the best algorithm.
    let minDistance = Infinity;
    let minDistanceWindow = undefined;

    for (const displayInfo of displayInfos) {
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

async function place(positionNumber: number): Promise<chrome.windows.Window> {
    const focusedWindow = await chrome.windows.getLastFocused();
    const display = await getClosestDisplay(focusedWindow);

    let left = display.workArea.left;
    let top = display.workArea.top;
    let width = display.workArea.width;
    let height = display.workArea.height;

    if ([3, 6, 9].includes(positionNumber))
        left += display.workArea.width / 2;
    if ([1, 2, 3].includes(positionNumber))
        top += display.workArea.height / 2;
    if ([1, 4, 7, 3, 6, 9].includes(positionNumber))
        width = display.workArea.width / 2;
    if ([7, 8, 9, 1, 2, 3].includes(positionNumber))
        height = display.workArea.height / 2;

    const placingBounds: chrome.windows.UpdateInfo = {
        top: Math.round(top),
        left: Math.round(left),
        width: Math.round(width),
        height: Math.round(height),
    } as const;

    console.log("Placing window", focusedWindow, "to", placingBounds);
    return chrome.windows.update(focusedWindow.id, placingBounds);
}

chrome.commands.onCommand.addListener(command => {
    console.log('Command received:', command);
    place(parseInt(command.slice(-1)));
});

chrome.runtime.onInstalled.addListener(details => {
    if (details.reason === "install")
        chrome.tabs.create({ url: "chrome://extensions/shortcuts" });
});
