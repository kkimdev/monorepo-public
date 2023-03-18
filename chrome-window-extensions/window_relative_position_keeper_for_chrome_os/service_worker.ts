// TODO: console.debug is not working for some reason so using console.log for now.

interface Point {
    x: number;
    y: number;
}

// TODO: Use this structure.
interface ScaledWindowBoundsInterface {
    left: number;
    top: number;
    width: number;
    height: number;
}

class AsyncTaskQueue {
    #taskQueue: (() => Promise<void>)[] = [];
    #resolve: (value: unknown) => void;
    #promise = new Promise((resolve, reject) => {
        this.#resolve = resolve;
    });

    constructor() {
        this.taskRunLoop();
    }

    push(task: () => Promise<void>) {
        this.#taskQueue.push(task);
        this.#resolve(undefined);
    }

    async getNextTask(): Promise<any> {
        if (this.#taskQueue.length === 0) {
            this.#promise = new Promise((resolve, _) => {
                this.#resolve = resolve;
            });
            await this.#promise;
        }
        return this.#taskQueue.shift();
    }

    async taskRunLoop(): Promise<void> {
        while (true) {
            const nextTask = await this.getNextTask();
            try {
                await nextTask();
            } catch (error) {
                console.error(error);
            }
        };
    }
};

const asyncTaskQueue = new AsyncTaskQueue();

async function chromeStorageSessionSet(key: string, value: any) {
    return await chrome.storage.session.set({ [key]: value });
}

async function chromeStorageSessionGet(key: string) {
    return (await chrome.storage.session.get([key]))[key];
}

async function getscaledWindowBounds() {
    return chromeStorageSessionGet('scaledWindowBounds');
}

async function setscaledWindowBounds(value: {}) {
    chromeStorageSessionSet('scaledWindowBounds', value);
}


function logger(...msgs: any) {
    // TODO: console.debug is not working for some reason so using console.log for now.
    console.log('\t'.repeat(Error().stack.split('\n').length - 2), ...msgs);
}

function computeCenter(box: chrome.system.display.Bounds): Point {
    return {
        x: box.left + box.width / 2,
        y: box.top + box.height / 2
    };
}

function computeDistance(point1: Point, point2: Point): number {
    return (point2.x - point1.x) ** 2 + (point2.y - point1.y) ** 2;
}

async function getClosestDisplay(window: chrome.windows.Window) {
    const chromeSystemDisplayGetInfoCache = await chrome.system.display.getInfo();
    if (chromeSystemDisplayGetInfoCache.length === 1)
        return chromeSystemDisplayGetInfoCache[0];

    const windowCenter = computeCenter(window as chrome.system.display.Bounds);

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
    for (const window of allWindows) {
        await addWindow(window);
    }
}

async function addWindow(window: chrome.windows.Window): Promise<void> {
    const closestDisplay = await getClosestDisplay(window);
    const newScaledWindowBound = {
        left: window.left / closestDisplay.workArea.width,
        width: window.width / closestDisplay.workArea.width,
        top: window.top / closestDisplay.workArea.height,
        height: window.height / closestDisplay.workArea.height,
    };
    let scaledWindowBounds = await getscaledWindowBounds();
    logger("addWindow", window, "from", scaledWindowBounds[window.id], "to", newScaledWindowBound);
    scaledWindowBounds[window.id] = newScaledWindowBound;
    await setscaledWindowBounds(scaledWindowBounds);
}

async function updateWindow(window: chrome.windows.Window): Promise<void> {
    logger("updateWindow", window);
    await addWindow(window);
}

async function removeWindow(windowId: number) {
    let scaledWindowBounds = await getscaledWindowBounds();
    logger("removeWindow", scaledWindowBounds[windowId]);
    if (!scaledWindowBounds[windowId]) {
        throw Error(`${windowId} to remove doesn't exists`);
    }
    delete scaledWindowBounds[windowId];
    await setscaledWindowBounds(scaledWindowBounds);
}

async function repositionWindows() {
    logger("repositionWindows start, displayInfos:", await chrome.system.display.getInfo());
    const scaledWindowBounds = await getscaledWindowBounds();

    for (const [windowId, value] of Object.entries(scaledWindowBounds)) {
        const scaledBound = scaledWindowBounds[windowId];
        const window = await chrome.windows.get(parseInt(windowId));
        const closestDisplay = await getClosestDisplay(window);
        if (window.state !== "normal") {
            logger("repositionWindows skip update window", window, "because the `window.state`", window.state, "is not `normal`.");
            continue;
        }

        const newBound = {
            left: Math.round(scaledBound.left * closestDisplay.workArea.width),
            width: Math.round(scaledBound.width * closestDisplay.workArea.width),
            top: Math.round(scaledBound.top * closestDisplay.workArea.height),
            height: Math.round(scaledBound.height * closestDisplay.workArea.height),
            state: "normal"
        } as const;

        logger("repositionWindows update window from", window, "to", newBound, "by", scaledBound);
        chrome.windows.update(window.id, newBound);
    }
    logger("repositionWindows end");
}

function addListeners() {
    chrome.windows.onCreated.addListener(
        (window) => {
            logger("chrome.windows.onCreated", window);
            asyncTaskQueue.push(async () => {
                await addWindow(window);
            });

        }
    );

    chrome.windows.onBoundsChanged.addListener(
        (window) => {
            logger("chrome.windows.onBoundsChanged", window);
            asyncTaskQueue.push(async () => {
                await updateWindow(window);
            });
        }
    );

    chrome.windows.onRemoved.addListener(
        (windowId) => {
            logger("chrome.windows.onRemoved", windowId);
            asyncTaskQueue.push(async () => {
                await removeWindow(windowId);
            });
        }
    );

    chrome.system.display.onDisplayChanged.addListener(
        () => {
            logger("chrome.system.display.onDisplayChanged");
            asyncTaskQueue.push(async () => {
                await repositionWindows();
            });
        }
    );
}

asyncTaskQueue.push(async () => {
    if (!await getscaledWindowBounds()) {
        await setscaledWindowBounds({});
        await addAllWindows()
    }
});

addListeners();
