"""Tests for the tenant-pack loader (config.loader), incl. the INFRA-04 AC."""

from __future__ import annotations

import pytest

from config.loader import (
    PackLoader,
    PackNotFoundError,
    PackParseError,
    PackValidationError,
    available_tenants,
    load_pack,
)
from config.schema import AutonomyMode, Channel

# A minimal-but-valid pack, parameterized on the instagram autonomy threshold.
PACK_TMPL = """
tenant_id = "{tid}"
display_name = "Test Tenant"

[voice]
skill = "brand-voice/test"

[channels.instagram]
enabled = true
[channels.instagram.autonomy]
mode = "auto"
threshold = {threshold}
"""


def _write_pack(packs_dir, tid="acme", threshold=0.9) -> None:
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / f"{tid}.toml").write_text(PACK_TMPL.format(tid=tid, threshold=threshold), encoding="utf-8")


# -- The shipped seed pack -------------------------------------------------- #


def test_loads_seed_ink_studio_pack(monkeypatch):
    # An operator .env can put INK_STUDIO_META_ACCESS_TOKEN into os.environ (any
    # earlier test touching load_local_env() leaks it process-wide) — clear it so
    # the resolve()-is-None assertion tests the pack, not the host machine.
    monkeypatch.delenv("INK_STUDIO_META_ACCESS_TOKEN", raising=False)
    pack = load_pack("ink-studio")
    assert pack.tenant_id == "ink-studio"
    assert pack.display_name == "Ink & Iron Tattoo Studio"
    assert pack.autonomy_for(Channel.INSTAGRAM).mode is AutonomyMode.AUTO
    assert pack.autonomy_for(Channel.GMAIL).mode is AutonomyMode.REVIEW
    assert pack.channel(Channel.INSTAGRAM).limits.per_day == 25
    # Secret is referenced by env name, not inlined.
    assert pack.secrets["meta_access_token"].env == "INK_STUDIO_META_ACCESS_TOKEN"
    assert pack.secrets["meta_access_token"].resolve() is None


def test_seed_pack_listed_in_available_tenants():
    assert "ink-studio" in available_tenants()


# -- Failure modes ---------------------------------------------------------- #


def test_missing_pack_raises_not_found(tmp_path):
    loader = PackLoader(tmp_path)
    with pytest.raises(PackNotFoundError):
        loader.load("nope")


def test_invalid_toml_raises_parse_error(tmp_path):
    (tmp_path / "broken.toml").write_text("this is = = not toml", encoding="utf-8")
    loader = PackLoader(tmp_path)
    with pytest.raises(PackParseError):
        loader.load("broken")


def test_schema_invalid_raises_validation_error(tmp_path):
    # threshold out of range -> schema validation failure (not a parse error).
    _write_pack(tmp_path, tid="bad", threshold=2.5)
    loader = PackLoader(tmp_path)
    with pytest.raises(PackValidationError):
        loader.load("bad")


def test_tenant_id_mismatch_raises(tmp_path):
    _write_pack(tmp_path, tid="declared")
    loader = PackLoader(tmp_path)
    # File is declared.toml but we ask for a different id than it declares.
    (tmp_path / "asked.toml").write_text(
        PACK_TMPL.format(tid="declared", threshold=0.9), encoding="utf-8"
    )
    with pytest.raises(PackValidationError):
        loader.load("asked")


# -- Caching: hot-reload vs restart ----------------------------------------- #


def test_load_caches_until_reload(tmp_path):
    _write_pack(tmp_path, tid="acme", threshold=0.9)
    loader = PackLoader(tmp_path)
    first = loader.load("acme")
    assert loader.load("acme") is first  # cached identity

    # Edit the file on disk; a plain load still returns the cached value...
    _write_pack(tmp_path, tid="acme", threshold=0.5)
    assert loader.load("acme") is first
    # ...until we explicitly reload (hot-reload).
    reloaded = loader.reload("acme")
    assert reloaded is not first
    assert reloaded.autonomy_for(Channel.INSTAGRAM).threshold == 0.5


# -- THE INFRA-04 ACCEPTANCE CRITERION -------------------------------------- #


def test_changing_pack_value_changes_behavior_without_code_change(tmp_path):
    """Editing the autonomy threshold in the pack flips a routing decision.

    No code changes between the two assertions — only the pack file value.
    """
    confidence = 0.80

    # Pack says: auto only above 0.90 -> 0.80 confidence must go to review.
    _write_pack(tmp_path, tid="acme", threshold=0.90)
    loader = PackLoader(tmp_path)
    pack = loader.load("acme")
    assert pack.autonomy_for(Channel.INSTAGRAM).decision(confidence) == "review"

    # Operator lowers the bar in the pack to 0.50 and we hot-reload.
    _write_pack(tmp_path, tid="acme", threshold=0.50)
    pack = loader.reload("acme")
    # Same confidence, same code — now it auto-acts.
    assert pack.autonomy_for(Channel.INSTAGRAM).decision(confidence) == "auto"
