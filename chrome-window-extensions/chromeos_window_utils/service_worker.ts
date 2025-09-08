/**
 * Checks a window's geometry and resizes it if it's snapped to a side.
 * This function is stateless and self-contained.
 * @param window The window to process.
 */
async function repositionWindow(window: chrome.windows.Window) {
    try {
        // 2. Get information about all connected displays.
        const displays = await chrome.system.display.getInfo();

        // 3. Find which display the window is primarily on.
        const windowCenterX = window.left + window.width / 2;
        const windowCenterY = window.top + window.height / 2;
        const display = displays.find(d =>
            windowCenterX >= d.workArea.left &&
            windowCenterX < (d.workArea.left + d.workArea.width) &&
            windowCenterY >= d.workArea.top &&
            windowCenterY < (d.workArea.top + d.workArea.height)
        );

        if (!display) {
            return; // Window is not on a detectable display.
        }

        // 4. Define the conditions for a "snapped" window.
        const { workArea } = display;
        const isFullHeight = window.height === workArea.height;

        // Use Math.round to account for display scaling inconsistencies.
        const halfWidth = Math.round(workArea.width / 2);
        const isLeftHalf = window.left === workArea.left && window.width === halfWidth;
        const isRightHalf = (window.left + window.width) === (workArea.left + workArea.width) && window.width === halfWidth;

        // 5. If all conditions are met, update the window's height.
        if (isFullHeight && (isLeftHalf || isRightHalf)) {
            console.log(`Resizing snapped window: ${window.id}`);
            
            // This action will trigger onBoundsChanged again, but on the next run,
            // the `isFullHeight` check will fail, preventing an infinite loop.
            chrome.windows.update(window.id, { height: workArea.height - 1 });
        }
    } catch (error) {
        console.error("Error during window repositioning:", error);
    }
}

// Add the listeners. Both events will trigger the same repositioning logic.
chrome.windows.onCreated.addListener(repositionWindow);
chrome.windows.onBoundsChanged.addListener(repositionWindow);

console.log("Simple Snap-n-Shrink extension loaded.");