const { createHash } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')

const project = path.resolve(__dirname, '..')
const frontend = path.join(project, 'src/len_bot/web/frontend')
const panel = path.join(frontend, '../static/dist')

function fingerprint() {
  const files = []
  function collect(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name === 'node_modules') continue
      const file = path.join(directory, entry.name)
      if (entry.isDirectory()) collect(file)
      else if (entry.isFile()) files.push(file)
    }
  }
  collect(frontend)
  files.push(__filename, path.join(project, 'scripts/collect_frontend_licenses.cjs'))
  const hash = createHash('sha256')
  for (const file of files.sort()) {
    hash.update(path.relative(project, file).split(path.sep).join('/'))
    hash.update('\0')
    hash.update(fs.readFileSync(file))
    hash.update('\0')
  }
  return hash.digest('hex')
}

function recordBuild() {
  fs.writeFileSync(path.join(panel, '.source-hash'), fingerprint())
}

function main() {
  const stamp = path.join(panel, '.source-hash')
  if (fs.existsSync(path.join(panel, 'index.html')) && fs.existsSync(stamp)
      && fs.readFileSync(stamp, 'utf8') === fingerprint()) return
  console.log('LenBot：正在更新控制面板。')
  for (const args of [['ci', '--no-audit', '--no-fund'], ['run', 'build']]) {
    const result = spawnSync('npm', args, { cwd: frontend, stdio: 'inherit', shell: process.platform === 'win32' })
    if (result.error) throw result.error
    if (result.status !== 0) process.exit(result.status ?? 1)
  }
}

if (process.argv[2] === '--record') recordBuild()
else main()
