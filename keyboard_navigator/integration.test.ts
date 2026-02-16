import { test, expect } from "playwright/test";
import { readFileSync } from "fs";
import { join } from "path";

const REPRO_URL = `file://${join(process.cwd(), "reproduce_bug.html")}`;

test.describe("Keyboard Navigator Integration", () => {
    test.beforeEach(async ({ page }) => {
        await page.goto(REPRO_URL);
        // Inject content script and styles if not already present via script tag in HTML
        // In reproduce_bug.html, we have <script src="content.js"></script>
        // and <style> block, so it should be pre-loaded.
    });

    test("should activate hints on Shift press", async ({ page }) => {
        await page.keyboard.press("Shift");

        // Check for presence of hints in shadow DOM
        const host = page.locator("#kb-nav-host");
        await expect(host).toBeAttached();

        // Check for hints inside shadow root
        const hintsCount = await page.evaluate(() => {
            const host = document.querySelector("#kb-nav-host");
            if (!host || !host.shadowRoot) return 0;
            return host.shadowRoot.querySelectorAll(".kb-nav-hint").length;
        });
        expect(hintsCount).toBeGreaterThan(26);
    });

    test("should highlight first character when typed", async ({ page }) => {
        await page.keyboard.press("Shift");
        await page.keyboard.type("A");

        const matchesCount = await page.evaluate(() => {
            const host = document.querySelector("#kb-nav-host");
            if (!host || !host.shadowRoot) return 0;
            return host.shadowRoot.querySelectorAll(".kb-nav-hint-match").length;
        });
        expect(matchesCount).toBeGreaterThan(0);

        const firstMatchText = await page.evaluate(() => {
            const host = document.querySelector("#kb-nav-host");
            const match = host?.shadowRoot?.querySelector(".kb-nav-hint-match");
            return match ? match.textContent : "";
        });
        expect(firstMatchText).toBe("A");
    });

    test("should unhighlight character when backspaced", async ({ page }) => {
        await page.keyboard.press("Shift");
        await page.keyboard.type("A");

        // Verify highlight exists
        let matchesCount = await page.evaluate(() => {
            const host = document.querySelector("#kb-nav-host");
            return host?.shadowRoot?.querySelectorAll(".kb-nav-hint-match").length || 0;
        });
        expect(matchesCount).toBeGreaterThan(0);

        await page.keyboard.press("Backspace");

        // Verify highlight is GONE
        matchesCount = await page.evaluate(() => {
            const host = document.querySelector("#kb-nav-host");
            return host?.shadowRoot?.querySelectorAll(".kb-nav-hint-match").length || 0;
        });
        expect(matchesCount).toBe(0);

        // Verify all hints are visible again (not filtered)
        const filteredCount = await page.evaluate(() => {
            const host = document.querySelector("#kb-nav-host");
            return host?.shadowRoot?.querySelectorAll(".kb-nav-hint-filtered").length || 0;
        });
        expect(filteredCount).toBe(0);
    });

    test("should select element when full code is typed", async ({ page }) => {
        await page.keyboard.press("Shift");

        // Find a hint code
        const code = await page.evaluate(() => {
            const host = document.querySelector("#kb-nav-host");
            const firstHint = host?.shadowRoot?.querySelector(".kb-nav-hint");
            return firstHint ? firstHint.textContent : "";
        });
        expect(code?.length).toBe(2);

        // Track clicks
        await page.evaluate(() => {
            window.clickedButtons = [];
            document.querySelectorAll("button").forEach(btn => {
                btn.onclick = () => window.clickedButtons.push(btn.textContent);
            });
        });

        for (const char of code!) {
            await page.keyboard.type(char);
        }

        // Wait for click to be registered
        await page.waitForFunction(() => window.clickedButtons.length > 0);

        const clickedCount = await page.evaluate(() => window.clickedButtons.length);
        expect(clickedCount).toBe(1);
    });

    test("should deactivate on Escape", async ({ page }) => {
        await page.keyboard.press("Shift");
        await page.keyboard.press("Escape");

        const isVisible = await page.evaluate(() => {
            const container = document.querySelector("#kb-nav-host")?.shadowRoot?.querySelector("#kb-nav-container");
            return container ? window.getComputedStyle(container).display !== "none" : false;
        });
        // Note: deactivation might have a 150ms delay due to closing animation
        await page.waitForTimeout(300);

        const isVisibleAfterDelay = await page.evaluate(() => {
            const container = document.querySelector("#kb-nav-host")?.shadowRoot?.querySelector("#kb-nav-container");
            return container ? window.getComputedStyle(container).display !== "none" : false;
        });
        expect(isVisibleAfterDelay).toBe(false);
    });
});
