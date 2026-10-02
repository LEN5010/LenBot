#!/usr/bin/env node
"use strict";

const { parseArgs } = require("node:util");
const fs = require("node:fs/promises");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { execFileSync } = require("node:child_process");
const { chromium } = require("/opt/lenbot/browser/node_modules/playwright-core");

const usage = `Usage: lenbot-render input.html [--pdf out/document.pdf] [--screenshot out/page.png]
       [--print-preview out/print] [--width 1280 --height 900]

Render local HTML offline. Use inline or local assets.
--pdf            Write PDF, using CSS page size or A4 by default.
--screenshot     Write a full-page screen-layout PNG at the chosen viewport.
--print-preview  With --pdf, render each actual PDF page to <prefix>-<page>.png.
--width/--height Set the screen viewport; they do not change the PDF paper size.
--help, -h       Show this help without opening a browser.
The JSON result lists actual paths, viewport and PDF page count. Files are not registered for delivery.`;

async function main() {
  const { values, positionals } = parseArgs({ allowPositionals: true, options: {
    pdf: { type: "string" }, screenshot: { type: "string" },
    "print-preview": { type: "string" }, help: { type: "boolean", short: "h" },
    width: { type: "string", default: "1280" }, height: { type: "string", default: "900" },
  } });
  if (values.help) {
    console.log(usage);
    return;
  }
  if (positionals.length !== 1 || (!values.pdf && !values.screenshot)) {
    throw new Error(usage);
  }
  if (values["print-preview"] && !values.pdf) {
    throw new Error("--print-preview requires --pdf");
  }
  const viewport = { width: Number(values.width), height: Number(values.height) };
  if (!Object.values(viewport).every(value => Number.isSafeInteger(value) && value > 0)) {
    throw new Error("width and height must be positive integers");
  }
  const input = path.resolve(positionals[0]);
  await fs.access(input);
  for (const output of [values.pdf, values.screenshot, values["print-preview"]].filter(Boolean)) {
    await fs.mkdir(path.dirname(path.resolve(output)), { recursive: true });
  }
  const browser = await chromium.launch({
    executablePath: "/usr/local/bin/lenbot-chromium", headless: true,
    args: ["--disable-dev-shm-usage"],
  });
  const result = { input, network: "offline", viewport };
  try {
    const context = await browser.newContext({ viewport, offline: true });
    const page = await context.newPage();
    await page.goto(pathToFileURL(input).href, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    if (values.screenshot) {
      await page.screenshot({ path: values.screenshot, fullPage: true });
      result.screenshot = path.resolve(values.screenshot);
    }
    if (values.pdf) {
      await page.pdf({ path: values.pdf, format: "A4", printBackground: true, preferCSSPageSize: true });
      result.pdf = path.resolve(values.pdf);
      const info = execFileSync("pdfinfo", [result.pdf], {
        encoding: "utf8", env: { ...process.env, LC_ALL: "C" },
      });
      const pages = /^Pages:\s+(\d+)\s*$/m.exec(info);
      if (!pages) throw new Error(`pdfinfo did not report page count: ${info}`);
      result.pages = Number(pages[1]);
      if (values["print-preview"]) {
        result.print_previews = [];
        for (let page = 1; page <= result.pages; page++) {
          const prefix = path.resolve(`${values["print-preview"]}-${page}`);
          execFileSync("pdftoppm", ["-f", String(page), "-l", String(page), "-singlefile",
            "-scale-to", "1600", "-png", result.pdf, prefix], { stdio: ["ignore", "ignore", "pipe"] });
          result.print_previews.push(prefix + ".png");
        }
      }
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(result));
}

main().catch(error => {
  console.error(error.stack);
  process.exitCode = 1;
});
