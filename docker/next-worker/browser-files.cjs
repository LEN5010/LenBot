"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const { parseArgs } = require("node:util");
const cliRoot = "/opt/lenbot/browser/node_modules/playwright-core/lib/tools/cli-client";
const { Registry, createClientInfo } = require(`${cliRoot}/registry`);
const { Session } = require(`${cliRoot}/session`);
const { mime } = require("/opt/lenbot/browser/node_modules/playwright-core/lib/utilsBundle");

const commands = new Set(["attach-files", "upload", "download", "screenshot", "pdf"]);
const help = `Task file commands (same public browser session):
  attach-files <ref> <file>...   Attach /inputs or /workspace files to an observed file input.
  upload <file>                 Attach a task file to the already-open native file chooser.
  download <ref>                Click once, wait for the download, save under out/browser/.
  screenshot [ref] [--filename out/browser/name.png] [--type png|jpeg|webp] [--full-page] [--hires]
  pdf [--filename out/browser/name.pdf]
Files returned by these commands are task resources, not registered deliveries or QQ uploads.
Other commands use the installed Playwright CLI; run <command> --help for its syntax.`;

function refLocator(ref) {
  if (!/^(f[0-9]+)?e[0-9]+$/.test(ref)) throw new Error(`Expected an observed element ref, received ${JSON.stringify(ref)}`);
  return `page.locator(${JSON.stringify(`aria-ref=${ref}`)})`;
}

async function inputFile(value) {
  const filename = await fs.realpath(path.resolve(value));
  const root = ["/inputs", "/workspace"].find(root => filename.startsWith(`${root}/`));
  if (!root) throw new Error(`Upload file must belong to /inputs or /workspace: ${filename}`);
  const info = await fs.stat(filename);
  if (!info.isFile()) throw new Error(`Upload requires a regular file: ${filename}`);
  return { path: filename, name: path.basename(filename), size: info.size,
    mime_type: mime.getType(filename) || "application/octet-stream",
    scope: root === "/inputs" ? "inputs" : "workspace", relative_path: path.relative(root, filename) };
}

async function recordOutput(file, source) {
  const settings = JSON.parse(await fs.readFile("/run/lenbot/task-api.json", "utf8"));
  try {
    const response = await fetch(`${settings.base_url}/task/browser-file`, {
      method: "POST", headers: { Authorization: `Bearer ${settings.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ path: path.relative("/workspace", file), ...source }),
      signal: AbortSignal.timeout(settings.timeout_seconds * 1000),
    });
    const raw = await response.text();
    if (!response.ok) throw new Error(`Task resource response (${response.status}): ${raw}`);
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`File saved at ${file}; task resource recording failed: ${error.message}`, { cause: error });
  }
}

async function runFileCommand(argv) {
  const command = argv[0];
  const { values, positionals } = parseArgs({ args: argv.slice(1), allowPositionals: true, options:
    ["screenshot", "pdf"].includes(command) ? {
      filename: { type: "string" }, ...(command === "screenshot" ? {
        type: { type: "string" }, "full-page": { type: "boolean" }, hires: { type: "boolean" },
      } : {}),
    } : {},
  });
  const expected = command === "attach-files" ? positionals.length >= 2
    : command === "screenshot" ? positionals.length <= 1
    : command === "pdf" ? positionals.length === 0 : positionals.length === 1;
  if (!expected) throw new Error(help);

  const client = createClientInfo();
  const entry = (await Registry.load()).entry(client, "public");
  if (!entry) throw new Error("Public browser is not open; run lenbot-browser open <url> first");
  const session = new Session(entry);
  const run = async args => {
    const { text } = await session.run(client, args, { json: true });
    let result;
    try { result = JSON.parse(text); }
    catch (error) { throw new Error(`Invalid Playwright JSON: ${text.slice(0, 1000)}`, { cause: error }); }
    if (result.isError) throw new Error(text);
    return result;
  };
  const code = async body => {
    const result = await run({ _: ["run-code", `async page => { ${body} }`] });
    try { return JSON.parse(result.result); }
    catch (error) { throw new Error(`Invalid Playwright code result: ${JSON.stringify(result).slice(0, 1000)}`, { cause: error }); }
  };
  const pageSource = () => code("return { page_url: page.url(), page_title: await page.title() };");

  if (command === "attach-files" || command === "upload") {
    const files = await Promise.all((command === "upload" ? positionals : positionals.slice(1)).map(inputFile));
    if (command === "upload") await run({ _: ["upload", files[0].path] });
    else await code(`await ${refLocator(positionals[0])}.setInputFiles(${JSON.stringify(files.map(file => file.path))}); return null;`);
    return { status: "attached", files, ...await pageSource() };
  }

  const source = await pageSource();
  const directory = "/workspace/out/browser";
  await fs.mkdir(directory, { recursive: true });
  if (command === "download") {
    const locator = refLocator(positionals[0]);
    const target = await fs.mkdtemp(`${directory}/download-`);
    // The pinned CLI auto-saves to a non-unique name. This one command owns its
    // download instead; restore the native listeners for ordinary CLI actions.
    const download = await code(`
      const listeners = page.listeners('download');
      for (const listener of listeners) page.removeListener('download', listener);
      try {
        const [download] = await Promise.all([page.waitForEvent('download'), ${locator}.click()]);
        const name = download.suggestedFilename();
        if (!name || name === '.' || name === '..' || /[\\/\\\\\\x00\\r\\n]/.test(name))
          throw new Error('Invalid download filename: ' + JSON.stringify(name));
        const file = ${JSON.stringify(target)} + '/' + name;
        await download.saveAs(file);
        return { path: file, download_url: download.url() };
      } finally {
        for (const listener of listeners) page.on('download', listener);
      }
    `);
    const file = await recordOutput(download.path, { ...source, kind: "download", download_url: download.download_url });
    return { status: "saved", files: [file] };
  }

  const extension = command === "pdf" ? "pdf" : values.type || (values.filename ? path.extname(values.filename).slice(1) : "png");
  const file = values.filename ? path.resolve(values.filename)
    : path.join(await fs.mkdtemp(`${directory}/${command}-`), `page.${extension}`);
  if (!file.startsWith(`${directory}/`)) throw new Error(`Browser output must be under ${directory}: ${file}`);
  // Create without overwriting a prior task file; the native command writes it.
  await fs.mkdir(path.dirname(file), { recursive: true });
  await (await fs.open(file, "wx")).close();
  try { await run({ _: [command, ...positionals], ...values, filename: file }); }
  catch (error) { await fs.unlink(file); throw error; }
  const output = await recordOutput(file, { ...source, kind: command });
  return { status: "saved", files: [output] };
}

module.exports = { commands, help, runFileCommand };
