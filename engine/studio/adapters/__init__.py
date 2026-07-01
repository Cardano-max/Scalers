"""Future-ready SOURCE ADAPTERS — the seams where client data plugs in later.

The tattoo-studio pivot must run the SAME workflow on an uploaded CSV today and on the
client's Stribe / Mini-App CRM tomorrow, with only a config change at these seams (ADR
§4.1). So every source of leads / conversations / artists is a Protocol with:
  * a real impl that WORKS NOW (CSV / seeded / the DB conversation store), and
  * honest STUBS for the not-yet-connected systems that raise :class:`NotConfiguredError`
    with a clear "not connected yet" message — they NEVER fabricate a lead, a thread, or
    an artist to paper over a missing integration.

Nodes consume only the normalized domain models (:class:`Lead`, :class:`ConversationThread`,
:class:`Artist`); swapping CSV -> Stribe is invisible to them.
"""

from __future__ import annotations


class NotConfiguredError(RuntimeError):
    """Raised by a source whose backing integration is not connected yet.

    Carries a human-readable, honest message (e.g. "Stribe is not connected yet — upload
    a conversation file instead"). The interview/UI surfaces this verbatim rather than
    pretending the source has data."""


__all__ = ["NotConfiguredError"]
