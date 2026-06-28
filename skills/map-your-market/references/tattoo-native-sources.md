# Tattoo-native sources (retarget map) — map-your-market

The upstream pattern mined Reddit/HN/GitHub Issues/G2. We retarget to where
tattoo demand actually surfaces. The **method/phrasing is the asset; the channels
are not** (winning-strategies-kb.md). All access is via the research adapter
(`engine/research/`) — official APIs, TLS on, no scraping.

| Channel | What to mine | Signal kinds |
|---|---|---|
| r/tattoos, r/TattooDesigns, r/tattoo | "is this price fair", "first tattoo", regret/cover-up, "how do I pick an artist" threads | pain, demand |
| Instagram hashtags | style tags (`#fineline`, `#blackwork`, `#traditionaltattoo`) + **city/locale tags**; comment questions | pain, demand |
| Pinterest | saved flash + style boards (what they pin = what they want next) | demand |
| TikTok | process / healed-reveal tags + trending sounds; which formats pull | angle |

## Niche tuning

- Pull the tenant's actual style + city from the pack (`niche`, locale) so signals
  are local, not generic. Seed terms come from the pack / the artist's own tags.
- Keep the client's exact words as `evidence` — they feed the brand-voice +
  winning-angle KB downstream.

## Hard don'ts (from skills-dos-donts.md)

- No TLS-disabled fetching, no scraping that violates ToS, no `GITHUB_TOKEN`/`.env`
  harvesting — the upstream `fetch.py` doing this is **stripped**.
- Don't invent demand on thin data; report the gap.
