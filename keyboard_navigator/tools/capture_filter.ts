/**
 * Capture a screenshot demonstrating the extension's filtering behavior.
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

  console.log(`Navigating to ${TARGET_URL}...`);
  await page.goto(TARGET_URL, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  // Inject the extension's CSS and JS
  await page.addStyleTag({ content: css });
  await page.evaluate(js);
  await page.waitForTimeout(500);

  // Activate hints
  console.log("Activating hints...");
  await page.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Shift", code: "ShiftLeft" }));
    window.dispatchEvent(new KeyboardEvent("keyup", { key: "Shift", code: "ShiftLeft" }));
  });
  await page.waitForTimeout(1000);

  // Type a character to filter
  console.log("Typing 'a' to filter...");
  await page.keyboard.press("a");
  await page.waitForTimeout(1000);

  // Take the screenshot
  const screenshotPath = "filtered_screenshot.png";
  await page.screenshot({ path: screenshotPath });
  console.log(`Screenshot captured: ${screenshotPath}`);

  await browser.close();
}

capture().catch((err) => {
  console.error(err);
  process.exit(1);
});
