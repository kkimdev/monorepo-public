/**
 * Capture a screenshot of a real website with the Keyboard Navigator extension active.
 * Uses Playwright Chromium to visit a real URL, inject the extension, and activate hints.
 */
import { chromium } from "playwright";
import { readFileSync } from "fs";

const TARGET_URL = "https://en.wikipedia.org/wiki/Keyboard_shortcut";

async function capture() {
  const css = readFileSync("content.css", "utf-8");
  const js = readFileSync("content.js", "utf-8");

  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

  // Navigate to real website
  await page.goto(TARGET_URL, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  // Inject the extension's CSS and JS
  await page.addStyleTag({ content: css });
  await page.evaluate(js);
  await page.waitForTimeout(500);

  // Activate hints by dispatching Shift key events
  await page.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Shift", code: "ShiftLeft" }));
  });
  await page.waitForTimeout(100);
  await page.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent("keyup", { key: "Shift", code: "ShiftLeft" }));
  });

  // Wait for hints to render
  await page.waitForTimeout(500);

  // Take the screenshot
  await page.screenshot({ path: "screenshot1.png" });
  console.log(`Screenshot captured: screenshot1.png (${TARGET_URL})`);

  await browser.close();
}

capture().catch((err) => {
  console.error(err);
  process.exit(1);
});
