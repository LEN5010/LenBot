#!/usr/bin/env node
"use strict";

const nativeArgs = process.argv.slice(2);
for (const name of Object.keys(process.env)) delete process.env[name];
Object.assign(process.env, {
  PATH: "/usr/local/bin:/usr/bin:/bin",
  HOME: "/opt/lenbot/browser-home",
  XDG_CACHE_HOME: "/tmp/lenbot-browser-cache",
  XDG_CONFIG_HOME: "/tmp/lenbot-browser-config",
  TMPDIR: "/tmp",
  LANG: "C.UTF-8",
});

const fs = require("node:fs");
const cliModule = "/opt/lenbot/browser/node_modules/playwright-core/lib/tools/cli-client/program";
const configFile = "/run/lenbot/browser.json";
const browserBinary = "/usr/local/bin/lenbot-chromium";
const unsupportedCommands = new Set([
  "install", "install-browser", "attach", "detach", "kill-all",
]);
const boundOptions = new Set([
  "--config", "--session", "--s", "--json", "--browser", "--headed", "--persistent", "--profile",
  "--cdp", "--endpoint", "--extension", "--device", "--mobile",
  "--executable-path", "--proxy-server", "--proxy-bypass",
]);

async function main() {
  fs.accessSync(configFile, fs.constants.R_OK);
  fs.accessSync(browserBinary, fs.constants.X_OK);
  fs.mkdirSync("/workspace/.playwright", { recursive: true });
  process.chdir("/workspace");

  const command = nativeArgs[0];
  if (!command || (command.startsWith("-")
    && !["--help", "-h", "--version", "-v"].includes(command))) {
    throw new Error("lenbot-browser requires a command first, or --help/--version");
  }
  if (unsupportedCommands.has(command)) {
    throw new Error(`lenbot-browser does not support ${command} in the installed anonymous backend`);
  }
  for (const token of nativeArgs) {
    if (token === "--") break;
    if (token === "--no-json" || token.startsWith("-s") && !token.startsWith("--")
      || [...boundOptions].some(option => token === option || token.startsWith(`${option}=`)
        || token === `--no-${option.slice(2)}`)) {
      throw new Error(`lenbot-browser does not accept an override of its fixed browser binding: ${token}`);
    }
  }

  process.argv = [process.execPath, cliModule, "--session=public", "--json",
    ...(command === "open" ? [`--config=${configFile}`] : []), ...nativeArgs];
  const { program } = require(cliModule);
  await program();
}

main().catch(error => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
