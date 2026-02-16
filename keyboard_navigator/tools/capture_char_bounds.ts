/**
 * Capture a screenshot demonstrating character-level bounding box detection.
 */
import { chromium } from "playwright";
import { readFileSync, writeFileSync } from "fs";

const TARGET_URL = "https://instantdomainsearch.com/";

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

  // Showcase logic: find first characters and draw bounding boxes
  await page.evaluate(() => {
    const SELECTORS = 'a, button, input, textarea, select, label, summary, [role="button"], [role="link"], [role="checkbox"], [role="menuitem"], [role="tab"], [role="option"], [role="radio"], [role="switch"], [role="menuitemcheckbox"], [role="menuitemradio"], [onclick], [tabindex="0"], [contenteditable="true"], [role="textbox"], [hx-get], [hx-post], [hx-put], [hx-delete], [hx-patch]';

    const container = document.createElement('div');
    container.id = 'showcase-container';
    container.style.cssText = 'position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 2147483647;';
    document.body.appendChild(container);

    const targets = document.querySelectorAll(SELECTORS);
    console.log(`Found ${targets.length} potential targets`);

    targets.forEach(el => {
      // Basic visibility check
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;

      // Find first character
      let firstCharRange = null;
      const walk = (node) => {
        if (firstCharRange) return;
        if (node.nodeType === Node.TEXT_NODE) {
          const text = node.textContent;
          const match = text.match(/\S/);
          if (match) {
            const range = document.createRange();
            range.setStart(node, match.index);
            range.setEnd(node, match.index + 1);
            if (range.getBoundingClientRect().width > 0) {
              firstCharRange = range;
            }
          }
        } else if (node.nodeType === Node.ELEMENT_NODE) {
          if (window.getComputedStyle(node).display === 'none') return;
          for (const child of node.childNodes) walk(child);
        }
      };
      walk(el);

      if (firstCharRange) {
        const charRect = firstCharRange.getBoundingClientRect();
        const box = document.createElement('div');
        box.style.cssText = `
          position: absolute;
          left: ${charRect.left + window.scrollX}px;
          top: ${charRect.top + window.scrollY}px;
          width: ${charRect.width}px;
          height: ${charRect.height}px;
          border: 1px solid magenta;
          background: rgba(255, 0, 255, 0.2);
          box-sizing: border-box;
        `;
        container.appendChild(box);
      }
    });
  });

  // Take the screenshot
  const screenshotPath = "char_bounds_showcase.png";
  await page.screenshot({ path: screenshotPath });
  console.log(`Screenshot captured: ${screenshotPath}`);

  await browser.close();
}

capture().catch((err) => {
  console.error(err);
  process.exit(1);
});
