"""Per-tenant pack loader (INFRA-04, systemdesign §6.4).

The engine reads a tenant's config at run start via :func:`load_pack` (or a
:class:`PackLoader` instance). Packs are TOML files in a packs directory, one per
tenant (``<tenant_id>.toml``), parsed with the stdlib ``tomllib`` and validated
against :class:`~config.schema.TenantPack`.

Failure modes are explicit, typed errors so callers can distinguish "no such
tenant" from "the file is corrupt" from "the config violates the schema":

* :class:`PackNotFoundError` — no pack file for the tenant.
* :class:`PackParseError` — the file is not valid TOML.
* :class:`PackValidationError` — TOML parsed but the config is invalid.

Hot-reload vs restart: a loader caches each pack after first read. Pass
``reload=True`` (or call :meth:`PackLoader.reload`) to re-read from disk — that is
how changing a pack value takes effect without restarting the process or
changing code.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from config.schema import TenantPack

# Packs ship alongside the loader: engine/config/packs/<tenant_id>.toml
DEFAULT_PACKS_DIR = Path(__file__).resolve().parent / "packs"


class PackError(Exception):
    """Base error for pack loading."""


class PackNotFoundError(PackError):
    """No pack file exists for the requested tenant."""


class PackParseError(PackError):
    """The pack file is not valid TOML."""


class PackValidationError(PackError):
    """The pack parsed as TOML but does not satisfy the schema."""


class PackLoader:
    """Loads and caches typed tenant packs from a directory."""

    def __init__(self, packs_dir: Path | str = DEFAULT_PACKS_DIR) -> None:
        self.packs_dir = Path(packs_dir)
        self._cache: dict[str, TenantPack] = {}

    def path_for(self, tenant_id: str) -> Path:
        return self.packs_dir / f"{tenant_id}.toml"

    def load(self, tenant_id: str, *, reload: bool = False) -> TenantPack:
        """Return the tenant's pack, reading and validating it on first use.

        Cached after the first read; pass ``reload=True`` to re-read from disk
        (hot-reload). Raises a :class:`PackError` subclass on any failure.
        """
        if not reload and tenant_id in self._cache:
            return self._cache[tenant_id]

        path = self.path_for(tenant_id)
        if not path.is_file():
            raise PackNotFoundError(
                f"no pack for tenant {tenant_id!r} (looked for {path})"
            )

        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise PackParseError(f"pack {path} is not valid TOML: {exc}") from exc

        try:
            pack = TenantPack.model_validate(raw)
        except ValidationError as exc:
            raise PackValidationError(
                f"pack {path} failed schema validation:\n{exc}"
            ) from exc

        if pack.tenant_id != tenant_id:
            raise PackValidationError(
                f"pack {path} declares tenant_id={pack.tenant_id!r} "
                f"but was loaded as {tenant_id!r}"
            )

        self._cache[tenant_id] = pack
        return pack

    def reload(self, tenant_id: str) -> TenantPack:
        """Force a fresh read from disk (hot-reload)."""
        return self.load(tenant_id, reload=True)

    def available(self) -> tuple[str, ...]:
        """Tenant ids with a pack file present, sorted."""
        if not self.packs_dir.is_dir():
            return ()
        return tuple(sorted(p.stem for p in self.packs_dir.glob("*.toml")))


# A process-wide default loader for the shipped packs directory.
_default_loader = PackLoader()


def load_pack(tenant_id: str, *, reload: bool = False) -> TenantPack:
    """Load a tenant pack from the default packs directory (systemdesign §6.4)."""
    return _default_loader.load(tenant_id, reload=reload)


def available_tenants() -> tuple[str, ...]:
    """Tenant ids available in the default packs directory."""
    return _default_loader.available()


def describe_tenant(tenant_id: str) -> str:
    """Render an HONEST one-line studio descriptor for grounding prompts.

    The descriptor is the *account identity* line every prompt-builder leads with
    (``build_strategy_prompt``, ``contentrun._build_prompt``, the research prompts,
    the archetype router). It replaces the old hardcoded ``"a women-led tattoo
    studio"`` literal that fabricated an identity for whatever tenant happened to be
    running.

    Resolution is REAL-pack-only:

    * pack resolves with ``voice.positioning`` → ``"@{tenant_id} — {display_name}, {positioning}"``
    * pack resolves without positioning → ``"@{tenant_id} — {display_name}"``
    * no pack, or the pack fails to load → the bare handle ``"@{tenant_id}"``

    The no-pack case is deliberately empty of any niche/voice claim: a tenant with no
    pack on file (e.g. a real client not yet onboarded) gets ONLY its handle, never an
    invented studio description. This never raises — a corrupt/invalid pack degrades to
    the bare handle exactly like a missing one.
    """
    handle = f"@{tenant_id}"
    try:
        pack = load_pack(tenant_id)
    except PackError:
        return handle

    display = (pack.display_name or "").strip()
    positioning = (pack.voice.positioning or "").strip()
    if display and positioning:
        return f"{handle} — {display}, {positioning}"
    if display:
        return f"{handle} — {display}"
    return handle
