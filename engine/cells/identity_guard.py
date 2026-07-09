"""Foreign-tenant identity guard (CustomerAcq-wwy.7, the smoking gun).

The live evidence: three real HELD outreach drafts staged for ``skindesign``
customers were signed "it's Rae from **Ladies First**" — the FIXTURE tenant's
identity on the real client's customers. The pack-resolution bleed that fed the
fixture voice into the copywriter is fixed at the source (``resolve_brand_voice``),
but prompt fixes are not a guarantee: a model can still emit another studio's name.

This is the deterministic POST-generation net, mirroring :mod:`cells.offer_guard`
and :mod:`cells.personalization_guard`: before an outreach draft is staged for
tenant T, the copy must not contain ANY OTHER tenant's identity — their pack
``display_name`` ("Ladies First") or their handle/tenant id ("ladies8391"). A
violating draft is skipped with a concrete reason; it never reaches the pending
queue.

Precision-first: only OTHER tenants' identities violate — the sending tenant may
(and should) name itself. Matching is word-boundary + case-insensitive so
"Ladies First" is caught but ordinary copy ("ladies night flash event") that merely
shares a word is not. Identities come from the REAL packs directory
(:func:`config.loader.available_tenants`); a tenant with no pack contributes
nothing — the guard never fabricates an identity to match against.
"""

from __future__ import annotations

import re
from functools import lru_cache


def _despace(s: str) -> str:
    """Collapse a display name to its run-together brand token (``"Ladies First"`` ->
    ``"ladiesfirst"``) so a no-space rendering (``LadiesFirst``, ``@ladiesfirst``) is
    still caught."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


@lru_cache(maxsize=1)
def _tenant_identities() -> dict[str, tuple[str, ...]]:
    """Map of ``tenant_id -> (identity tokens)`` from the REAL packs on disk.

    Tokens per tenant are brand-DISTINCTIVE full identifiers only (never a single
    generic word like "tattoo"/"studio" that would false-positive on ordinary copy):
    the tenant id, the pack ``display_name``, and each run together (``"Ladies First"``
    -> ``"ladiesfirst"``) so a no-space rendering is caught. Every token is matched with
    word boundaries, so a token that is a substring of a normal word never trips.

    Best-effort: an unreadable pack contributes only its tenant id. Cached — packs are
    static per process (loader hot-reload is for values, not identity sweeps).
    """
    from config.loader import PackError, available_tenants, load_pack

    out: dict[str, tuple[str, ...]] = {}
    for tid in available_tenants():
        tokens: set[str] = {tid, _despace(tid)}
        try:
            pack = load_pack(tid)
            display = (pack.display_name or "").strip()
            if display:
                tokens.add(display)
                tokens.add(_despace(display))
        except PackError:
            pass
        # Drop empty/too-short tokens; a <4-char token risks matching ordinary words.
        out[tid] = tuple(sorted(t for t in tokens if len(t) >= 4))
    return out


def foreign_identity_violations(text: str, tenant_id: str) -> list[str]:
    """Every OTHER tenant's identity asserted in ``text`` — empty == clean.

    The HARD check, pure: a draft staged for ``tenant_id`` must not contain any other
    tenant's display name (spaced or run-together) or handle. Whitespace in ``text`` is
    normalized first so a newline-split name (``"Ladies\\nFirst"``) still matches the
    spaced token; the run-together token catches ``"LadiesFirst"`` / ``"@ladiesfirst"``.
    All matching is word-boundaried, so a brand token that is a substring of an ordinary
    word never false-positives. Returns human-readable violations naming the foreign
    tenant and the matched token, so the skip reason is concrete and debuggable.
    """
    if not (text or "").strip():
        return []
    norm = re.sub(r"\s+", " ", text)
    violations: list[str] = []
    for tid, tokens in _tenant_identities().items():
        if tid == tenant_id:
            continue
        for token in tokens:
            if re.search(rf"\b{re.escape(token)}\b", norm, re.IGNORECASE):
                violations.append(
                    f"foreign tenant identity (copy contains {token!r}, which is "
                    f"tenant {tid!r} — not the sending tenant {tenant_id!r})"
                )
                break  # one violation per foreign tenant is enough
    return violations


def foreign_identity_ok(text: str, tenant_id: str) -> bool:
    """True iff ``text`` asserts no other tenant's identity."""
    return not foreign_identity_violations(text, tenant_id)
