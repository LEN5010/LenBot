// Collect the installed npm tree's own metadata and notices, not guessed licenses.
const fs = require('node:fs');
const path = require('node:path');
const [modules, output] = process.argv.slice(2);
if (!modules || !output || process.argv.length !== 4) throw new Error('Expected node_modules and output directory');
fs.mkdirSync(output, {recursive:true});
const records = [];
const noticeName = /^(licen[cs]es?|copying|notice|copyright)(?:[._-].*|$)/i;
function packageDirectory(directory) {
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
  if (typeof manifest.name !== 'string' || typeof manifest.version !== 'string') {
    throw new Error(`Installed package is missing name/version: ${directory}`);
  }
  const relative = path.relative(modules, directory);
  const destination = path.join(output, 'packages', relative);
  fs.mkdirSync(destination, {recursive:true});
  const notices = fs.readdirSync(directory).filter(name => noticeName.test(name));
  for (const name of notices) fs.cpSync(path.join(directory, name), path.join(destination, name), {recursive:true});
  const metadata = {name:manifest.name, version:manifest.version,
    license:manifest.license ?? null, repository:manifest.repository ?? null,
    homepage:manifest.homepage ?? null, installed_path:relative, notice_files:notices};
  fs.writeFileSync(path.join(destination, 'metadata.json'), JSON.stringify(metadata, null, 2));
  records.push(metadata);
  const nested = path.join(directory, 'node_modules');
  if (fs.existsSync(nested)) walk(nested);
}
function walk(directory) {
  for (const name of fs.readdirSync(directory).sort()) {
    if (name.startsWith('.')) continue;
    const location = path.join(directory, name);
    if (!fs.statSync(location).isDirectory()) continue;
    if (name.startsWith('@')) {
      for (const child of fs.readdirSync(location).sort()) packageDirectory(path.join(location, child));
    } else packageDirectory(location);
  }
}
walk(modules);
fs.writeFileSync(path.join(output, 'index.json'), JSON.stringify({
  source:'Installed npm packages from this build, including build-only dependencies',
  packages:records,
  missing_license_metadata:records.filter(item => item.license === null).map(item => item.installed_path),
  missing_notice_files:records.filter(item => item.notice_files.length === 0).map(item => item.installed_path),
}, null, 2));
