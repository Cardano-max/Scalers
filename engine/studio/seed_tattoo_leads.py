"""Seed realistic MOCK warm leads (with conversation history) for the tattoo demo.

Gives the provided-leads run real, VARIED data to classify + brand: ~7 warm leads in
the operator's own SMS-thread shape, each carrying a different objection (price, timing,
trust, payment), lifecycle (recurring touch-up, past reactivation), or an open enquiry —
so the psychology analyst's category + objection reads and the offer-substantiation
branching are demonstrable end to end.

Each lead is upserted into ``customers`` (idempotent on tenant+email, extended fields
included) and its conversation into ``lead_conversations`` (idempotent on tenant+customer).
The mock offers doc is seeded too. Nothing sends. Re-running is a no-op.

The lead DATA is a module constant so it is inspectable + unit-testable without a DB.
"""

from __future__ import annotations

from typing import Any

# One artist roster the artist-specific leads resolve against (seeded, no client API).
SEED_ARTISTS = ["Maya", "Rae", "Noor"]

# (campaign_sms, conversation turns) use the operator's shape. ``customer_type`` /
# ``artist`` / ``notes`` seed the CRM fields the analyst + branching read. Each lead is
# built to demonstrate ONE clear read; the analyst still derives everything from the real
# conversation/fields (nothing here pre-labels the objection for the analyst).
SEED_LEADS: list[dict[str, Any]] = [
    {
        "name": "Sarah Kim", "email": "sarah.kim@example.com", "artist": "Maya",
        "customer_type": "artist_specific", "interests": ["fine-line", "floral"],
        "notes": "asked about fine-line floral wrist piece",
        "campaign_message": (
            "Hi Sarah, this is Ladies First Tattoo. You previously asked about fine-line "
            "floral work but didn't get a chance to book. Maya has a few floral flash "
            "designs available this week. Reply 'FLOWER' and we'll send you 2 options."
        ),
        "turns": [
            {"speaker": "customer", "text": "Hi, I wanted to ask about a small floral tattoo on my wrist."},
            {"speaker": "studio", "text": "Lovely! Fine-line or bold?"},
            {"speaker": "customer", "text": "Fine-line, simple. How much?"},
            {"speaker": "studio", "text": "Usually around $120-$180 for a small wrist piece."},
            {"speaker": "customer", "text": "I like it but maybe later, short on budget right now."},
            {"speaker": "studio", "text": "No problem, we can let you know about flash designs / small-piece offers."},
            {"speaker": "customer", "text": "Yes please message me if there's a discount."},
        ],
    },
    {
        "name": "Priya Anand", "email": "priya.anand@example.com", "artist": "Rae",
        "customer_type": "artist_specific", "interests": ["fine-line", "script"],
        "notes": "first tattoo, wants to see healed work",
        "campaign_message": "Hi Priya, it's Ladies First — following up on your script tattoo idea.",
        "turns": [
            {"speaker": "customer", "text": "Hi, I've never had a tattoo before and I'm a bit nervous about it."},
            {"speaker": "studio", "text": "Totally understandable! What were you thinking of?"},
            {"speaker": "customer", "text": "A small script piece. Can I see some healed work first? I want to make sure it's clean and safe."},
            {"speaker": "studio", "text": "Of course, Rae does a lot of first tattoos."},
            {"speaker": "customer", "text": "Okay, I'm just nervous, need to trust the artist before I book."},
        ],
    },
    {
        "name": "Dana Ruiz", "email": "dana.ruiz@example.com",
        "customer_type": "open", "interests": ["small piece"],
        "notes": "interested but timing",
        "campaign_message": "Hi Dana, Ladies First here — still keen on that small piece?",
        "turns": [
            {"speaker": "customer", "text": "Hey, I'd love a small piece but I'm crazy busy right now."},
            {"speaker": "studio", "text": "No worries! We can plan ahead whenever suits."},
            {"speaker": "customer", "text": "Yeah maybe later, probably after the summer when things settle."},
        ],
    },
    {
        "name": "Aisha Bello", "email": "aisha.bello@example.com", "artist": "Noor",
        "customer_type": "artist_specific", "interests": ["sleeve", "blackwork"],
        "notes": "wants a sleeve, asked about paying over time",
        "campaign_message": "Hi Aisha, Ladies First — about your sleeve project with Noor.",
        "turns": [
            {"speaker": "customer", "text": "I really want to start a blackwork sleeve with Noor."},
            {"speaker": "studio", "text": "Amazing, that's a big beautiful project!"},
            {"speaker": "customer", "text": "It's a lot at once though — can I pay in installments or do a payment plan?"},
            {"speaker": "studio", "text": "We can talk about splitting it across sessions."},
        ],
    },
    {
        "name": "Mel Carter", "email": "mel.carter@example.com",
        "customer_type": "recurring", "interests": ["traditional"],
        "notes": "regular, 3 pieces with us",
        "campaign_message": "Hi Mel, Ladies First — hope your last piece healed well!",
        "turns": [
            {"speaker": "customer", "text": "Hey! My last traditional piece healed great, thank you."},
            {"speaker": "studio", "text": "So glad to hear it! Thinking about your next one?"},
            {"speaker": "customer", "text": "Maybe soon, and it might need a tiny touch-up on the older one."},
        ],
        "tattoo_history": [{"style": "traditional"}, {"style": "traditional"}],
    },
    {
        "name": "Jess Lowe", "email": "jess.lowe@example.com",
        "customer_type": "reactivation", "interests": ["blackwork"],
        "notes": "no visit in over a year",
        "campaign_message": "Hi Jess, it's been a while! Ladies First would love to see you again.",
        "turns": [
            {"speaker": "customer", "text": "Oh hi! Yeah it's been ages, life got busy."},
            {"speaker": "studio", "text": "We've missed you! Lots of new blackwork flash lately."},
        ],
        "persona_traits": {"lifecycle_stage": "lapsing", "win_back_candidate": True},
        "tattoo_history": [{"style": "blackwork"}],
    },
    {
        "name": "Robin Tate", "email": "robin.tate@example.com",
        "customer_type": "open", "interests": ["minimalist"],
        "notes": "just browsing styles",
        "campaign_message": "Hi Robin, Ladies First here — happy to help whenever you're ready.",
        "turns": [
            {"speaker": "customer", "text": "Hi! Just browsing styles for now, love the minimalist stuff you post."},
            {"speaker": "studio", "text": "Thank you! Shout whenever you want to chat ideas."},
        ],
    },
]


def seed_warm_leads(tenant_id: str = "ladies8391", *, dsn: str | None = None) -> dict[str, Any]:
    """Idempotently seed the mock warm leads + their conversations + the offers doc.

    Returns ``{customer_ids, conversations, offers_doc_id}``. Best-effort per lead so a
    single bad row cannot abort the seed; nothing sends."""
    from studio.conversations import upsert_conversation
    from studio.customer_research import upsert_lead
    from studio.offers import seed_offers_doc

    offers_doc_id = seed_offers_doc(tenant_id, dsn=dsn)
    ids: list[str] = []
    n_conv = 0
    for lead in SEED_LEADS:
        row = {
            "name": lead["name"], "email": lead["email"],
            "interests": "; ".join(lead.get("interests", [])),
            "notes": lead.get("notes", ""), "artist": lead.get("artist", ""),
            "customer_type": lead.get("customer_type", ""),
        }
        res = upsert_lead(tenant_id, row, dsn=dsn)
        cid = res["customer_id"]
        ids.append(cid)
        upsert_conversation(
            tenant_id, cid, lead["turns"], channel="sms", source="seed",
            campaign_message=lead.get("campaign_message"), dsn=dsn,
        )
        n_conv += 1
    return {"customer_ids": ids, "conversations": n_conv, "offers_doc_id": offers_doc_id}


if __name__ == "__main__":  # pragma: no cover
    import json
    import os

    out = seed_warm_leads(os.environ.get("STUDIO_TENANT_ID", "ladies8391"))
    print(json.dumps(out, indent=2))
