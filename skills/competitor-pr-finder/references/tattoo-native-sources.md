# Tattoo-native sources (retarget map) — competitor-pr-finder

Upstream found press coverage + journalists. For a tattoo studio the equivalent
"where competitors win attention" surface is **ad creatives**. The method is the
asset. All access via the research adapter (`engine/research/`) — official APIs,
TLS on, no scraping.

| Channel | What to mine | Output |
|---|---|---|
| Meta Ad Library (via **Foreplay**, primary) | competitor studio ads; long run-time ≈ a working angle | angle, hook, format, run-time evidence |
| Instagram | top-engaged organic reels/posts | hook + format that pulls |

## Match confidence (false-positive guard)

Set `Creative.confidence` from match strength (handle/name + locale). Below the
router's floor (0.5) the match is **flagged for operator review**, never dropped
or auto-trusted — tattoo handles collide often (city + style names repeat).

## Hard don'ts (skills-dos-donts.md)

- Official APIs only; no scraping bans; no TLS-disabled fetching.
- Extract the **angle/pattern**, never reproduce a competitor's imagery or copy —
  our output is original and on-voice, grounded not copied.
