"""Console read API for ju1.5 (SD-FRONTEND) — campaign-example memory + draft lineage.

Three additive, read-only endpoints the Next.js console binds:

* ``GET /studio/campaign-examples``            — the tenant's real campaign example
  library (ju1.2 store) + extracted patterns, for the Campaign Memory view.
* ``GET /studio/campaign-examples/{id}/screenshot`` — streams the example's source
  screenshot from the LOCAL client-data directory. The filename comes ONLY from the
  example's own DB row (never from the request), so there is no path traversal and
  nothing is ever uploaded anywhere.
* ``GET /studio/action/{id}/lineage``          — the review-queue draft lineage:
  source CSV / customer name-email-phone / artist / studio / offer / CTA / channel /
  campaign examples referenced. Fields the system cannot ground are HONEST ``None``
  (the console renders "missing", never a blank fake). ``examples`` is ``[]`` until
  ju1.4 wires per-draft example provenance into the generator.

Kept in its own module (not ``studio/agui.py``) so the hot AG-UI module stays
collision-free for in-flight beads; mounted from ``engine/main.py``.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

# Where the operator's local client-data lives (screenshots referenced by the
# campaign_examples rows). Same default the ju1.1 importer CLI uses
# (studio/client_import.py); env-overridable for other machines/CI.
_CLIENT_DATA_DIR = "SCALERS_CLIENT_DATA_DIR"
_DEFAULT_CLIENT_DATA = "C:/Users/Links/Desktop/CustomerAcq/client-data"


def _client_data_dir() -> Path:
    return Path(os.environ.get(_CLIENT_DATA_DIR) or _DEFAULT_CLIENT_DATA)


def _jsonable(value: Any) -> Any:
    """Coerce DB row values (Decimal / datetime) to JSON-safe primitives."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


@router.get("/studio/campaign-examples")
def campaign_examples(tenant_id: str) -> dict:
    """The tenant's campaign-example memory: real examples + extracted patterns.

    Honest-empty for a tenant with none (never a fabricated example). Each example
    gains ``screenshot_url`` when its screenshot file is actually present locally —
    the console renders the image through the sibling endpoint below.
    """
    from studio.campaign_examples_store import get_examples, get_patterns

    examples = []
    for row in get_examples(tenant_id):
        ex = {k: _jsonable(v) for k, v in row.items()}
        shot = _resolve_screenshot(row.get("source_screenshot"))
        ex["screenshot_url"] = (
            f"/studio/campaign-examples/{row['id']}/screenshot" if shot else None
        )
        examples.append(ex)
    patterns = [{k: _jsonable(v) for k, v in row.items()} for row in get_patterns(tenant_id)]
    return {"tenantId": tenant_id, "examples": examples, "patterns": patterns}


def _resolve_screenshot(source_screenshot: str | None) -> Path | None:
    """Locate the example's screenshot under the local client-data dir.

    ``source_screenshot`` is the stored Slack file id (with or without extension).
    Only that DB value is used — never request input — and the result must stay
    inside the screenshots dir. Missing file -> None (honest-missing)."""
    if not source_screenshot:
        return None
    base = _client_data_dir() / "screenshots"
    name = Path(source_screenshot).name  # strips any stray path components
    candidates = [name] if "." in name else [f"{name}{ext}" for ext in (".png", ".jpg", ".jpeg")]
    for cand in candidates:
        p = base / cand
        if p.is_file():
            return p
    return None


@router.get("/studio/campaign-examples/{example_id}/screenshot")
def campaign_example_screenshot(example_id: str) -> FileResponse:
    """Stream the example's source screenshot from the LOCAL client-data dir.

    The filename is read from the example's own row; a request can only ever name
    an example id. 404 when the example or its file is absent."""
    from studio.campaign_examples_store import _connect  # same store, same DSN chain

    try:
        with _connect(None) as conn:
            row = conn.execute(
                "SELECT source_screenshot FROM campaign_examples WHERE id = %s",
                (example_id,),
            ).fetchone()
    except Exception:
        row = None
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown campaign example {example_id!r}")
    path = _resolve_screenshot(row["source_screenshot"])
    if path is None:
        raise HTTPException(status_code=404, detail="screenshot file not present locally")
    media = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(path, media_type=media)


def _dossier_field(dossier: dict, key: str) -> tuple[Any, str | None]:
    """A DossierField's ``(value, source)`` — honest ``(None, None)`` when absent."""
    f = dossier.get(key)
    if isinstance(f, dict):
        return f.get("value"), f.get("source")
    return None, None


@router.get("/studio/action/{action_id}/lineage")
def action_lineage(action_id: str) -> dict:
    """Draft lineage for the review queue (ju1.5): where this draft CAME from.

    Assembled from the staged action's dossier context + the customers/artists
    tables. Every field the system cannot ground is ``None`` — the console renders
    an explicit "missing", never a fabricated value.
    """
    from actions.store import get_action

    action = get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail=f"unknown action {action_id!r}")

    dossier: dict[str, Any] = {}
    if action.context:
        try:
            ctx = json.loads(action.context)
            if isinstance(ctx, dict) and isinstance(ctx.get("dossier"), dict):
                dossier = ctx["dossier"]
        except (ValueError, TypeError):
            dossier = {}

    name, _ = _dossier_field(dossier, "name")
    email, _ = _dossier_field(dossier, "email")
    phone, _ = _dossier_field(dossier, "phone")
    cta, _ = _dossier_field(dossier, "recommended_cta")
    _, angle_source = _dossier_field(dossier, "best_angle")
    # The dossier records a substantiated offer only as a "+offer:CODE" suffix on
    # the angle's source (dossier.py) — surface the code; None when no real offer.
    offer = None
    if angle_source and "+offer:" in angle_source:
        offer = angle_source.rsplit("+offer:", 1)[1] or None

    customer_id = dossier.get("customer_id")
    source_file, artist, studio = _customer_lineage(action.tenant_id, customer_id, email)

    return {
        "actionId": action.id,
        "runId": action.run_id,
        "channel": action.channel,
        "sourceFile": source_file,
        "customer": {"id": customer_id, "name": name, "email": email, "phone": phone},
        "artist": artist,
        "studio": studio,
        "offer": offer,
        "cta": cta,
        # Per-draft example provenance is ju1.4's generator wiring; until it lands
        # this is honestly empty and the console says so.
        "examples": [],
        "limitedPersonalization": dossier.get("limited_personalization"),
        "personalizationNote": dossier.get("personalization_note"),
    }


def _customer_lineage(
    tenant_id: str, customer_id: str | None, email: str | None
) -> tuple[str | None, str | None, str | None]:
    """(source_file, artist, studio) for the draft's customer — honest Nones.

    ``source_file``/``artist``/``shop`` live on the customers row (ju1.1 ext
    columns); when the customer names an artist but no shop, the artist_studios
    mapping (13-artists.sql) supplies the studio. Any store hiccup -> all None."""
    if not customer_id and not email:
        return None, None, None
    try:
        from studio.campaign_examples_store import _connect

        with _connect(None) as conn:
            clauses, params = ["tenant_id = %s"], [tenant_id]
            if customer_id:
                clauses.append("id = %s")
                params.append(customer_id)
            else:
                clauses.append("lower(email) = lower(%s)")
                params.append(email)
            cust = conn.execute(
                "SELECT source_file, artist, shop FROM customers WHERE "
                + " AND ".join(clauses) + " LIMIT 1",
                params,
            ).fetchone()
            if cust is None:
                return None, None, None
            source_file = cust.get("source_file")
            artist = cust.get("artist") or None
            studio = cust.get("shop") or None
            if artist and not studio:
                srow = conn.execute(
                    "SELECT s.studio_name FROM artists a "
                    "JOIN artist_studios s ON s.artist_id = a.id "
                    "WHERE a.tenant_id = %s AND lower(a.name) = lower(%s) LIMIT 1",
                    (tenant_id, artist),
                ).fetchone()
                studio = srow["studio_name"] if srow else None
            return source_file, artist, studio
    except Exception:
        return None, None, None


def mount_console_api(app: FastAPI) -> None:
    """Attach the ju1.5 console read endpoints to the portal app."""
    app.include_router(router)
