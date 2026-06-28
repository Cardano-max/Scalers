# Tattoo-native communities (retarget map) — where-your-customer-lives

Upstream searched Reddit/HN/DuckDuckGo. We retarget to where tattoo clients
gather. The method is the asset; channels are not. All access via the research
adapter (`engine/research/`) — official APIs, TLS on, no scraping.

| Channel | Communities | Entry tactic |
|---|---|---|
| Reddit | r/tattoos, r/TattooDesigns, r/tattoo, city subreddits | answer "is this fair / first tattoo / how to pick an artist" with genuine guidance; link only when asked |
| Instagram hashtags | style tags + **city/locale** tags (`#brooklyntattoo`) | reply with technique value; geo-tag posts so local clients surface them |
| Pinterest | style/flash boards | publish flash as richly-described pins so saves feed discovery |
| TikTok | process/reveal tags + trending sounds | post short reveals on trending sounds; pin a "how to book" comment |

## Niche tuning

- Pull the tenant's style + city from the pack so communities are local. A
  small-city niche may be thin — return the nearest larger community + a note.

## Hard don'ts (skills-dos-donts.md)

- No TLS-disabled fetching, no ToS-violating scraping, no `GITHUB_TOKEN`/`.env`
  harvesting (upstream `fetch.py` stripped). Keep the human approval gate — entry
  tactics are guidance, nothing auto-posts.
