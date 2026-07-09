# demo_studio demo data — Fenmore Tattoo (SYNTHETIC / FICTIONAL)

> ⚠️ **100% FICTIONAL, company-owned demo asset** for the tlv.6 end-to-end demo
> slice. NOT real people, NOT a real studio. Every email uses the RFC-2606 reserved
> `@example.com` domain and every phone uses the reserved `555-0100–555-0199`
> fiction range (with Denver area code 303), so **nothing here resolves to a real
> person or business**. Zero bleed from the real client `skindesign` or the
> synthetic `ladies8391` / `ink-studio` tenants. No sensitive-attribute inference:
> every field is a booking fact only.
>
> **Name collision check (2026-07-09):** the studio name, all 5 artist names, and
> every customer name were **web-searched** to confirm none matches a real tattoo
> studio, a real business/brand, or a notable public figure (Wikipedia / public
> artist-author-athlete-musician). Names shared only with private, non-notable
> individuals are unavoidable and acceptable; any hit on a notable person or real
> business was renamed. (Fixes the earlier "Copper Fox Tattoo" / "Priya Anand"
> collisions.)
>
> Authored by writer 2026-07-09. Brand-voice bundle for this tenant lives at
> `skills/brand-voice/tenants/demo_studio/` + pack `engine/config/packs/demo_studio.toml`.

## `customers.csv` — 11 fictional customers

| Column | Meaning | Format |
|---|---|---|
| `name` | customer display name | fictional |
| `email` | upsert key for idempotent import | `*@example.com` (reserved) |
| `phone` | contact | E.164 `+1303555 01xx` (reserved fiction range) |
| `interests` | tattoo styles/themes | `;`-delimited |
| `last_visit` | last appointment | ISO `yyyy-mm-dd` |
| `objection` | the booking objection to overcome | short phrase |
| `preferred_artist` | resident artist (see brand-dna roster) | one of Elliot Prewitt / Wren Halloway / Rafi Sadeghi / Marlow Deitch / Gwen Lindqvist |
| `notes` | booking-relevant persona detail | free text (`;`-delimited if multi) |

5 of 11 are **lapsed** (last visit before 2025-08) to exercise the win-back demo
(`"build me a win-back campaign for my lapsed clients"`); the rest are recent
clients / fresh consult leads. All 5 resident artists are referenced.

## Ingestion (eng4 / tlv.6 — shape is adjustable)

- **Base fields** (`name, email, phone`) import today via the existing
  `studio.client_import.parse_customers_csv` unchanged — it ingests those three and
  **reports** the five rich columns as `unknown_columns` (never drops silently, never
  crashes). Verified: 11 rows parsed, 0 skipped, 0 invalid phones.
- **Rich persona fields** (`interests … notes`) need the demo seeder to read them
  into memory/personas so the strategist can ground per-customer proposals. Either
  extend the demo seeder to consume this single rich CSV, **or** split into
  `customers.csv` (base) + `personas.csv` (email-keyed) — trivial from this file.
- `email` is the idempotent upsert key, so re-seeding is safe (no dupes).

> This shape is writer's proposal pending eng4 confirmation (tenant_id, single-rich
> vs split CSV + who ingests the rich fields, final path). eng4: adjust freely on the
> PR — the content is fixed, only the plumbing shape is open.
