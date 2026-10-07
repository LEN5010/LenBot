"""Set one release version and check its notes in a clean checkout; never commits, tags or pushes.

Publishing starts when the matching v<version> tag is pushed, which stays an explicit step for the maintainer.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r'\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?')
NOTES = ROOT / 'changelogs'
PLACEHOLDER = '（发布时填写'


def notes_problems(version: str) -> list[str]:
    """What keeps the notes from being published as the Release body (the release workflow checks the same)."""
    path = NOTES / f'v{version}.md'
    if not path.exists():
        return [f'缺少 {path.relative_to(path.parents[1])}']
    notes = path.read_text(encoding='utf-8')
    problems = []
    if not notes.startswith(f'# LenBot {version}\n'):
        problems.append(f'第一行应为 "# LenBot {version}"，实际是 {notes.splitlines()[0]!r}')
    if PLACEHOLDER in notes:
        problems.append(f'仍有 {PLACEHOLDER}…） 占位')
    return problems


def set_version(version: str) -> str:
    path = ROOT / 'pyproject.toml'
    text = path.read_text(encoding='utf-8')
    previous = tomllib.loads(text)['project']['version']
    # The first top-level version line is [project].version; tomllib above confirms where it is.
    updated, count = re.subn(r'(?m)^version = "[^"]*"$', f'version = "{version}"', text, count=1)
    if count != 1 or tomllib.loads(updated)['project']['version'] != version:
        raise ValueError('pyproject.toml has no plain [project] version line to update')
    path.write_text(updated, encoding='utf-8')
    return previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', help='PEP 440 release version without the v prefix, e.g. 0.2.0 or 0.2.0rc1')
    args = parser.parse_args()
    if not VERSION.fullmatch(args.version):
        raise SystemExit(f'版本号应形如 0.2.0 或 0.2.0rc1，不带 v：{args.version!r}')
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, check=True)
    if status.stdout.strip():
        raise SystemExit('工作区有未提交的修改，请先提交或暂存后再准备发版。')
    tags = subprocess.run(['git', 'tag', '--list', f'v{args.version}'], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.split()
    if tags:
        raise SystemExit(f'标签 v{args.version} 已存在，同一版本不重新发布。')

    previous = set_version(args.version)
    subprocess.run(['uv', 'lock'], cwd=ROOT, check=True)
    print(f'版本 {previous} → {args.version}，已更新 pyproject.toml 和 uv.lock。')
    global NOTES
    version_notes = ROOT / 'changelogs' / f'v{args.version}.md'
    if version_notes.exists():
        NOTES = version_notes
    problems = notes_problems(args.version)
    if problems:
        print(f'changelogs/v{args.version}.md 还不能作为 Release 正文：')
        for problem in problems:
            print('  - ' + problem)
    print('之后由你确认并执行：')
    print('  git diff')
    print(f'  git commit -am "发布 {args.version}"')
    print(f'  git tag v{args.version}')
    channel = '预发布' if re.search(r'(a|b|rc)\d+$', args.version) else '正式'
    print(f'  git push origin v{args.version}   # 推送标签即执行完整构建并发布{channel}版本')


if __name__ == '__main__':
    main()
