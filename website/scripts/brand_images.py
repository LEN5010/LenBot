"""Render the README banner and the social preview from the tile icon.

The text is drawn by the local browser, so run this on a machine with PingFang SC or another CJK font.
"""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

BRAND = Path(__file__).resolve().parents[1] / 'public/brand'
ZH = ('基于大模型的 QQ 群聊机器人', 'OneBot v11 · 网页管理面板 · Python 插件')
EN = ('An LLM chat bot for QQ groups', 'OneBot v11 · Web panel · Python plugins')
# name: (width, height, icon size, name size, tagline size, text, show detail line)
IMAGES = {
    'lenbot-banner.png': (1280, 400, 216, 104, 34, ZH, True),
    'lenbot-banner.en.png': (1280, 400, 216, 104, 34, EN, True),
    'lenbot-social-preview.png': (1280, 640, 300, 128, 40, ZH, False),
}
PAGE = """<!doctype html><meta charset="utf-8"><style>
body{{margin:0;width:{w}px;height:{h}px;display:flex;align-items:center;justify-content:center;gap:{gap}px;
  background:#FBEFF3;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;color:#2A2530}}
.icon{{width:{icon}px;height:{icon}px;filter:drop-shadow(0 18px 36px rgba(180,71,106,.22))}}
.icon svg{{width:100%;height:100%}}
h1{{margin:0;font-size:{name}px;font-weight:600;letter-spacing:-.02em;line-height:1}}
p{{margin:.55em 0 0;font-size:{tag}px;font-weight:600}}
p.detail{{margin-top:.7em;font-size:{detail}px;font-weight:400;color:#6B6870}}
</style><div class="icon">{svg}</div><div><h1>LenBot</h1><p>{tagline}</p>{detail_line}</div>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=BRAND)
    args = parser.parse_args()
    svg = (BRAND / 'lenbot-mark-tile.svg').read_text(encoding='utf-8')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, (width, height, icon, title, tag, (tagline, detail_text), detail) in IMAGES.items():
            page = browser.new_page(viewport={'width': width, 'height': height})
            page.set_content(PAGE.format(
                w=width, h=height, gap=round(icon * 0.33), icon=icon, svg=svg, name=title, tag=tag,
                detail=round(tag * 0.7), tagline=tagline,
                detail_line=f'<p class="detail">{detail_text}</p>' if detail else ''))
            page.screenshot(path=args.output / name)
            page.close()
            print(args.output / name)
        browser.close()


if __name__ == '__main__':
    main()
