"""Identity Guardian — is this public profile really OUR customer?

The enrichment path finds public-web candidates by searching the customer's name.
Names are not identities: "Maya Torres" matches thousands of people, and molding a
draft around a STRANGER's public life is both wrong and creepy. This module is the
deterministic, evidence-based gate between "a page mentioning the name" and "a fact
about our customer".

For each candidate hit it scores the match against the customer's FIRST-PARTY
identifiers (what they gave the studio: email, phone, Instagram handle, city,
stated interests) and returns one of four verdicts:

    confirmed  — a hard identifier matched (their IG handle / email / phone /
                 business domain appears in the source). Usable, cited.
    likely     — full-name match corroborated by an independent soft signal
                 (their city, or a stated interest). Usable, cited, labelled.
    uncertain  — name-only (or weaker). NOT usable for personalization: the
                 draft continues on first-party data alone and the candidate is
                 surfaced as "unverified" with the reason.
    rejected   — a hard identifier CONFLICT (e.g. the hit is a different
                 Instagram account than the handle on file, or a different
                 person's name). Never used, never shown as evidence.

Everything here is PURE and deterministic — string/token evidence, no model call,
no network — so the verdict is explainable ("matched: ig_handle, city") and
reproducible in the trace. The guardian can only DEMOTE data (verified-in,
verified-out); it never invents an identity signal.
"""

from __future__ import annotations

import re
from typing import Any

VERDICTS = ("confirmed", "likely", "uncertain", "rejected")

# Hosts whose path segment is an account handle — a mismatch there is a hard
# conflict, not just "no match" (the page IS somebody's profile).
_HANDLE_HOSTS = ("instagram.com", "facebook.com", "tiktok.com", "x.com", "twitter.com")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _tokens(text: Any) -> set[str]:
    # Apostrophes split ("Chen's" -> chen + s) so possessives never break a
    # name match; the stray single-letter fragments are filtered by callers.
    return set(re.findall(r"[a-z0-9]+", _norm(text)))


def _digits(text: Any) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _url_handle(url: str) -> str | None:
    """The account handle when the URL is a social profile page, else None."""
    m = re.match(r"https?://(?:www\.)?([^/]+)/([^/?#]+)", _norm(url))
    if not m:
        return None
    host, seg = m.group(1), m.group(2).strip("@")
    if any(h in host for h in _HANDLE_HOSTS) and seg not in (
        "p", "reel", "reels", "stories", "explore", "share", "profile.php", "pages",
    ):
        return seg
    return None


def score_identity_match(
    customer: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Score ONE candidate hit against the customer's first-party identifiers.

    ``customer``: the DB facts (``name``, ``email``, ``phone``, ``ig_handle``,
    ``city``, ``interests``). ``candidate``: the research hit (``url``, ``quote``).

    Returns ``{"verdict", "confidence", "evidence": [...], "concerns": [...]}`` —
    evidence and concerns name the exact signals, so the trace shows WHY."""
    url = _norm(candidate.get("url"))
    quote = _norm(candidate.get("quote") or candidate.get("snippet") or "")
    haystack = f"{url} {quote}"
    hay_tokens = _tokens(haystack)

    evidence: list[str] = []
    concerns: list[str] = []
    hard = soft = 0

    # ── hard identifiers (any one is decisive) ─────────────────────────────────
    ig = _norm(customer.get("ig_handle")).lstrip("@")
    url_handle = _url_handle(url)
    if ig:
        if (url_handle and url_handle == ig) or f"@{ig}" in quote or f"/{ig}" in url:
            hard += 1
            evidence.append(f"instagram handle on file (@{ig}) appears in the source")
        elif url_handle and url_handle != ig:
            concerns.append(
                f"source is a different account (@{url_handle}) than the handle on "
                f"file (@{ig})"
            )
            return {"verdict": "rejected", "confidence": 0.05,
                    "evidence": evidence, "concerns": concerns}

    email = _norm(customer.get("email"))
    if email:
        local, _, domain = email.partition("@")
        if email in haystack:
            hard += 1
            evidence.append("email on file appears in the source")
        elif domain and domain not in (
            "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
            "example.com",
        ) and domain in haystack:
            hard += 1
            evidence.append(f"business email domain on file ({domain}) matches the source")

    phone = _digits(customer.get("phone"))
    if len(phone) >= 7 and phone[-7:] in _digits(haystack):
        hard += 1
        evidence.append("phone number on file appears in the source")

    # ── name (necessary, never sufficient) ─────────────────────────────────────
    name_tokens = {t for t in _tokens(customer.get("name")) if len(t) > 1}
    name_hit = bool(name_tokens) and name_tokens.issubset(hay_tokens)
    partial_name = bool(name_tokens & hay_tokens) and not name_hit
    if name_hit:
        evidence.append("full name matches")
    elif partial_name:
        concerns.append("only part of the name matches")
    elif name_tokens:
        concerns.append("customer name does not appear in the source")

    # ── soft corroboration ─────────────────────────────────────────────────────
    city = _norm(customer.get("city"))
    if city and city in haystack:
        soft += 1
        evidence.append(f"city on file ({customer.get('city')}) appears in the source")
    for interest in customer.get("interests") or []:
        it = _tokens(interest)
        if it and it.issubset(hay_tokens):
            soft += 1
            evidence.append(f"stated interest ({interest}) appears in the source")
            break

    # ── verdict ────────────────────────────────────────────────────────────────
    if hard:
        conf = min(0.98, 0.85 + 0.05 * hard + 0.02 * soft)
        return {"verdict": "confirmed", "confidence": round(conf, 2),
                "evidence": evidence, "concerns": concerns}
    if name_hit and soft:
        return {"verdict": "likely", "confidence": round(min(0.8, 0.6 + 0.1 * soft), 2),
                "evidence": evidence, "concerns": concerns}
    if name_hit:
        concerns.append(
            "name-only match — could be any person with this name; not used for "
            "personalization"
        )
        return {"verdict": "uncertain", "confidence": 0.3,
                "evidence": evidence, "concerns": concerns}
    return {"verdict": "rejected", "confidence": 0.05,
            "evidence": evidence,
            "concerns": concerns or ["no identifying signal matches the customer"]}


def partition_verified(
    customer: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    """Split candidate facts into usable vs not, annotating every one.

    Returns::

        {"verified":   [fact + identity {verdict, confidence, evidence}, ...],
         "unverified": [fact + identity ..., ...],   # uncertain — shown, never used
         "rejected_count": n,
         "counts": {"confirmed": n, "likely": n, "uncertain": n, "rejected": n}}

    Only ``verified`` (confirmed/likely) may flow into memories/prompts. The
    guardian never upgrades a fact — absence of evidence keeps it out."""
    verified: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    counts = {v: 0 for v in VERDICTS}
    for cand in candidates or []:
        scored = score_identity_match(customer, cand)
        counts[scored["verdict"]] += 1
        annotated = {**cand, "identity": scored}
        if scored["verdict"] in ("confirmed", "likely"):
            verified.append(annotated)
        elif scored["verdict"] == "uncertain":
            unverified.append(annotated)
        # rejected: counted, never carried — a stranger's page is not evidence
    return {"verified": verified, "unverified": unverified,
            "rejected_count": counts["rejected"], "counts": counts}
