"""Log in to the demo panel and save the site screenshots (run demo_instance.py first)."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {'home': '/#/host/overview', 'persona': '/#/host/persona', 'plugins': '/#/host/plugins?view=discover',
         'scenes': '/#/host/scenes', 'connection': '/#/host/system?tab=connection'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', default='http://127.0.0.1:18088')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'public/screenshots')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900}, device_scale_factor=2, locale='zh-CN')
        page.goto(args.panel + '/#/login')
        page.get_by_label('用户名').fill('demo')
        page.get_by_label('密码').fill('demo-password')
        page.get_by_role('button', name='登录').click()
        page.wait_for_url('**/#/host/**')
        for name, path in PAGES.items():
            page.goto(args.panel + path)
            page.wait_for_load_state('networkidle')
            page.mouse.move(0, 0)
            # Entrance animations and the live-status dot settle within a second.
            page.wait_for_timeout(1500)
            page.screenshot(path=args.output / f'{name}.png')
            print(args.output / f'{name}.png')
        browser.close()


if __name__ == '__main__':
    main()
