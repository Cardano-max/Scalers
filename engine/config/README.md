# config — per-tenant packs (INFRA-04)

The engine and frontend are generic; a client's niche lives entirely in a typed
**pack** here. Loaded at run start (systemdesign §5.3 / §6.4).

```python
from config import load_pack, Channel

pack = load_pack("ink-studio")                    # typed TenantPack, schema-validated
ig = pack.autonomy_for(Channel.INSTAGRAM)         # per-channel autonomy
decision = ig.decision(confidence=0.82)           # "auto" | "review" | "off"
token = pack.secrets["meta_access_token"].resolve()  # from env; never inlined
```

## Layout

| File | Role |
|------|------|
| `schema.py` | `TenantPack` + nested models (`AutonomyConfig`, `ChannelConfig`, `RateLimits`, `ScheduleConfig`, `SecretRef`, …) |
| `loader.py` | `load_pack(tenant_id)` / `PackLoader`; TOML via stdlib `tomllib`; typed errors |
| `packs/<tenant>.toml` | one file per tenant (the niche) |

## What a pack holds

Per-channel autonomy (mode + confidence threshold), rate limits, brand-voice
ref, channel set, suppression source, sending domain, schedule, enabled research
sources, and env-resolved secret refs.

## Behavior without code change

Change a pack value (e.g. an autonomy `threshold`) and the engine's decisions
change with no code change. A loader caches packs; `reload()` (or
`load(..., reload=True)`) re-reads from disk — hot-reload vs restart.

## Failure modes

`load_pack` raises a typed error, never a silent default: `PackNotFoundError`
(no file), `PackParseError` (bad TOML), `PackValidationError` (schema violation).

## Adding a tenant

Drop a `packs/<tenant_id>.toml` (see `packs/ink-studio.toml`); the filename must
match the `tenant_id` inside. Never inline secrets — reference an env var via
`[secrets.<name>] env = "..."`.
