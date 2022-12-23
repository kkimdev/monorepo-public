// import * as T from "chrome-types";

// TODO: console.debug is not working for some reason so using console.log for now.

function computeCenter(box) {
    return {
        x: box.left + box.width / 2,
        y: box.top + box.height / 2
    };
}

function computeDistance(point1, point2) {
    return (point2.x - point1.x) ** 2 + (point2.y - point1.y) ** 2;
}

async function getClosestDisplay(window) {
    const displayInfos = await chrome.system.display.getInfo();
    if (displayInfos.length === 1)
        return displayInfos[0];

    const windowCenter = computeCenter(window);
    // logger(displayInfos);
    // logger('windowCenter', windowCenter);

    // TODO: minimum distance logic might not be the best algorithm.
    let minDistance = Infinity;
    let minDistanceWindow = undefined

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

async function place(positionNumber: number): Promise<void> {
    const focusedWindow = await chrome.windows.getLastFocused();
    const display = await getClosestDisplay(focusedWindow);
    const displayWorkArea = display.workArea;

    let left = displayWorkArea.left;
    let top = displayWorkArea.top;
    let width = displayWorkArea.width;
    let height = displayWorkArea.height;

    if ([3, 6, 9].includes(positionNumber))
        left += displayWorkArea.width / 2;
    if ([1, 2, 3].includes(positionNumber))
        top += displayWorkArea.height / 2;
    if ([1, 4, 7, 3, 6, 9].includes(positionNumber))
        width = displayWorkArea.width / 2;
    if ([7, 8, 9, 1, 2, 3].includes(positionNumber))
        height = displayWorkArea.height / 2;

    const placingBounds = {
        top: Math.round(top),
        left: Math.round(left),
        width: Math.round(width),
        height: Math.round(height),
        state: "normal",
    } as const;
    console.log("Placing window", focusedWindow, "to", placingBounds);
    chrome.windows.update(focusedWindow.id, placingBounds);
}

chrome.commands.onCommand.addListener((command) => {
    console.log('Command received:', command);
    place(parseInt(command.slice(-1)));
});
