"""Log in to the demo panel and save the site screenshots (run demo_instance.py first)."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {'home': '/#/host/overview', 'persona': '/#/host/persona', 'plugins': '/#/host/plugins',
         'scenes': '/#/host/scenes', 'update': '/#/host/system'}


def prepare_plugins(page) -> None:
    """Open the clock plugin, enable it once and pick two groups, the way an owner would."""
    page.get_by_text('clock', exact=True).click()
    page.wait_for_timeout(500)
    switch = page.get_by_label('启用这个插件')
    if not switch.is_checked():
        switch.check()
        page.get_by_role('button', name='保存并应用').click()
        page.wait_for_timeout(2500)
    for group in ('周末桌游局', '读书会'):
        chip = page.locator('.scene-picks .v-chip', has_text=group)
        if 'mdi-check' not in (chip.inner_html()):
            chip.click()
            page.wait_for_timeout(2500)
    page.mouse.move(0, 0)
    page.wait_for_timeout(5000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', default='http://127.0.0.1:18088')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'public/screenshots')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900}, device_scale_factor=2, locale='zh-CN')
        response = page.request.post(args.panel + '/api/auth/login', data={'username': 'demo', 'password': 'demo-password'})
        assert response.ok, response.text()
        for name, path in PAGES.items():
            page.goto(args.panel + path)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(800)
            if name == 'plugins':
                prepare_plugins(page)
            page.screenshot(path=args.output / f'{name}.png')
            print(args.output / f'{name}.png')
        browser.close()


if __name__ == '__main__':
    main()
