"""Tests for deterministic idempotency-key derivation (systemdesign §3, HARN-04)."""

from sideeffects import Channel, idempotency_key


def test_same_logical_action_yields_same_key():
    """Same (tenant, channel, target, content) -> identical key, always."""
    a = idempotency_key("nw", Channel.OUTREACH, "bayside-pg", "hello world")
    b = idempotency_key("nw", Channel.OUTREACH, "bayside-pg", "hello world")
    assert a == b


def test_different_content_yields_different_key():
    """A change in content must change the key (so it is a distinct effect)."""
    a = idempotency_key("nw", Channel.OUTREACH, "bayside-pg", "draft one")
    b = idempotency_key("nw", Channel.OUTREACH, "bayside-pg", "draft two")
    assert a != b


def test_key_structure_matches_design():
    """Key is `tenant:channel:target:contenthash` — the §3 example shape."""
    key = idempotency_key("nw", Channel.OUTREACH, "bayside-pg", "x")
    tenant, channel, target, content_hash = key.split(":")
    assert tenant == "nw"
    assert channel == "outreach"
    assert target == "bayside-pg"
    assert content_hash and all(c in "0123456789abcdef" for c in content_hash)


def test_key_is_stable_across_processes():
    """Derivation uses a stable hash (not Python's salted hash()), so the
    same inputs produce the same key in a fresh interpreter — required for
    the DB UNIQUE constraint to dedupe across runs/crashes."""
    import subprocess
    import sys

    code = (
        "from sideeffects import Channel, idempotency_key;"
        "print(idempotency_key('nw', Channel.OUTREACH, 'bayside-pg', 'x'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == idempotency_key("nw", Channel.OUTREACH, "bayside-pg", "x")
