/**
 * Automated demo walkthrough for Keyboard Navigator.
 * Uses Playwright's built-in video recording for reliability.
 */
import { chromium } from "playwright";
import { readFileSync, writeFileSync, renameSync, readdirSync, rmSync, existsSync } from "fs";
import { join } from "path";

const TARGET_URL = "https://en.wikipedia.org/wiki/Keyboard_shortcut";
const OFFSET_FILE = "offset.txt";
const VIDEO_DIR = "videos";

async function capture() {
  // Cleanup previous runs
  if (existsSync(VIDEO_DIR)) rmSync(VIDEO_DIR, { recursive: true, force: true });
  if (existsSync("demo_raw.webm")) rmSync("demo_raw.webm");

  const css = readFileSync("content.css", "utf-8");
  const js = readFileSync("content.js", "utf-8");

  const startTime = Date.now();
  console.log(`Start time: ${startTime}`);

  const browser = await chromium.launch({
    headless: false,
    executablePath: process.env.CHROMIUM_PATH,
    args: [
      "--disable-infobars",
      "--no-sandbox",
      "--window-size=1280,720"
    ]
  });

  const context = await browser.newContext({
    recordVideo: {
      dir: VIDEO_DIR,
      size: { width: 1280, height: 720 }
    },
    viewport: { width: 1280, height: 720 }
  });

  const page = await context.newPage();

  console.log(`Navigating to ${TARGET_URL}...`);
  await page.goto(TARGET_URL, { waitUntil: "networkidle" });

  const loadTime = Date.now();
  // Playwright video starts at context creation.
  // We need to calculate offset relative to context creation, which is approximately startTime.
  // precision isn't perfect but usually very close.
  const offsetSeconds = (loadTime - startTime) / 1000;
  console.log(`Page loaded. Offset: ${offsetSeconds}s`);
  writeFileSync(OFFSET_FILE, offsetSeconds.toString());

  // Wait a small buffer
  await page.waitForTimeout(500);

  // Inject content
  await page.addStyleTag({ content: css });
  await page.evaluate(js);

  // Interaction Sequence
  console.log("Activating hints...");
  await page.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Shift", code: "ShiftLeft" }));
  });
  await page.waitForTimeout(100);
  await page.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent("keyup", { key: "Shift", code: "ShiftLeft" }));
  });
  await page.waitForTimeout(1000);

  console.log("Typing filter...");
  await page.keyboard.type("a", { delay: 100 });
  await page.waitForTimeout(1000);

  console.log("Clearing filter...");
  await page.keyboard.press("Backspace");
  await page.waitForTimeout(500);

  console.log("Deactivating hints...");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1000);

  await context.close(); // Saves the video
  await browser.close();

  // Find the video file
  if (existsSync(VIDEO_DIR)) {
    const videoFiles = readdirSync(VIDEO_DIR);
    if (videoFiles.length > 0) {
      const videoPath = join(VIDEO_DIR, videoFiles[0]);
      console.log(`Video saved to: ${videoPath}`);
      renameSync(videoPath, "demo_raw.webm");
      console.log("Renamed to demo_raw.webm");
    } else {
      console.error("No video file found in videos/ directory");
      process.exit(1);
    }
  } else {
    console.error("videos/ directory not created");
    process.exit(1);
  }

  console.log("Demo finished.");
}

capture().catch((err) => {
  console.error(err);
  process.exit(1);
});
