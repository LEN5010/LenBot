"""One declared palette for every HTML card.

`bilibili/template.html` is where these values came from -- it is the visual
source of truth, and its CSS still spells them literally. Anything rendered
afterwards imports the tokens from here instead of copying hex codes, so
"unified design" is something the code enforces rather than something a second
template happens to match today.

The reference plugin drew its schedule in a separate warm-beige scheme
(#f3ebdf ground, #c56d49 kicker) while its push card was pink. Keeping the
schedule's layout ideas -- corner glow, tinted time block, day grouping -- but
moving them onto these tokens is what makes the two read as one product.
"""
from __future__ import annotations

THEME_CSS = """
  :root {
    --ink: #23232b;
    --ink-soft: #34343d;
    --body: #50505a;
    --muted: #858590;
    --faint: #9b9ba6;
    --hairline: #c3b6bc;

    --brand: #ff5f91;
    --brand-deep: #ee5d8c;
    --bar: linear-gradient(90deg, #ff75a5, #ff9fbd 48%, #ffd9e6);

    --tint: #fff0f5;
    --tint-soft: #fff5f8;
    --line: #ffe1eb;
    --line-neutral: #f1eff3;
    --line-dashed: #ecdfe5;

    --surface: #ffffff;
    --surface-muted: #faf9fb;
    --link: #1388c5;

    --card-radius: 68px;
    --block-radius: 28px;
    --pill-radius: 20px;
    --card-pad: 68px;

    /* The reference schedule's two corner blooms, recoloured onto the
       pink system so they read as decoration rather than a second brand. */
    --glow-warm: #ffe7ef;
    --glow-cool: #f2eaf3;
  }

  html, body { width: 1200px; margin: 0; padding: 0; background: transparent; }
  body {
    font-family: "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .card {
    position: relative; width: 1200px; box-sizing: border-box;
    padding: 64px var(--card-pad) 56px;
    border-radius: var(--card-radius); overflow: hidden;
    background:
      radial-gradient(1100px 520px at 92% 7%, var(--glow-warm) 0%, rgba(255, 238, 244, 0) 62%),
      linear-gradient(180deg, #ffffff 0%, var(--tint-soft) 100%);
    border: 1px solid var(--line);
  }
  .topbar {
    position: absolute; top: 0; left: 0; right: 0; height: 12px;
    background: var(--bar);
  }
  .bloom { position: absolute; border-radius: 50%; pointer-events: none; }
  .bloom--warm { top: -120px; left: -140px; width: 520px; height: 380px; background: var(--glow-warm); }
  .bloom--cool { top: -90px; right: -110px; width: 400px; height: 300px; background: var(--glow-cool); }
"""
