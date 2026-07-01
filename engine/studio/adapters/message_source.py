"""Message source adapters — a lead's conversation turns from any backing system.

``UploadedConversationFile`` parses an uploaded transcript now (via
:func:`studio.reason_history.parse_conversation_text`); ``DbConversationSource`` reads
the persisted :mod:`studio.conversations` store. ``StribeSmsThread`` / ``MiniAppCrmNotes``
are honest stubs that raise :class:`~studio.adapters.NotConfiguredError`. None fabricate a
conversation — a lead with no thread yields ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from studio.adapters import NotConfiguredError
from studio.reason_history import parse_conversation_text


@dataclass
class ConversationThread:
    """A normalized conversation for one lead — the turns the ABSA extractor reads."""

    customer_id: str | None = None
    turns: list[dict[str, str]] = field(default_factory=list)
    channel: str | None = None
    source: str = "upload"
    campaign_message: str | None = None

    @property
    def has_turns(self) -> bool:
        return bool(self.turns)


@runtime_checkable
class MessageSourceProtocol(Protocol):
    """Yields the :class:`ConversationThread` for a lead, or ``None`` when none exists."""

    name: str

    def thread_for(self, customer_id: str) -> ConversationThread | None:
        ...


class UploadedConversationFile:
    """Parse an uploaded transcript (the operator's ``Customer:``/``Studio:`` or
    slash-separated shape) into a :class:`ConversationThread` (works now).

    Honest: returns ``None`` for unparseable / empty input rather than a guessed dialogue."""

    name = "uploaded conversation file"

    def __init__(self, raw_text: str, *, customer_id: str | None = None,
                 channel: str | None = None, campaign_message: str | None = None) -> None:
        self._raw = raw_text or ""
        self._customer_id = customer_id
        self._channel = channel
        self._campaign_message = campaign_message

    def thread_for(self, customer_id: str) -> ConversationThread | None:
        turns = parse_conversation_text(self._raw)
        if not turns:
            return None
        return ConversationThread(
            customer_id=customer_id or self._customer_id, turns=turns,
            channel=self._channel, source="upload",
            campaign_message=self._campaign_message,
        )


class DbConversationSource:
    """Read a lead's stored conversation from the :mod:`studio.conversations` table
    (works now — the durable home seeded/uploaded threads live in)."""

    name = "conversation store"

    def __init__(self, tenant_id: str, *, dsn: str | None = None) -> None:
        self._tenant_id = tenant_id
        self._dsn = dsn

    def thread_for(self, customer_id: str) -> ConversationThread | None:
        from studio.conversations import get_conversation

        row = get_conversation(self._tenant_id, customer_id, dsn=self._dsn)
        if not row or not row.get("turns"):
            return None
        return ConversationThread(
            customer_id=customer_id, turns=row["turns"], channel=row.get("channel"),
            source=row.get("source") or "db", campaign_message=row.get("campaign_message"),
        )


class StribeSmsThread:
    """STUB: a lead's SMS thread from Stribe. Not connected yet -> honest error."""

    name = "Stribe SMS (not connected)"

    def thread_for(self, customer_id: str) -> ConversationThread | None:
        raise NotConfiguredError(
            "Stribe SMS threads are not connected yet — upload the conversation as a "
            "file for now. The same turns contract will be populated once Stribe is wired."
        )


class MiniAppCrmNotes:
    """STUB: a lead's CRM notes/messages from the Mini-App. Not connected yet."""

    name = "Mini-App CRM notes (not connected)"

    def thread_for(self, customer_id: str) -> ConversationThread | None:
        raise NotConfiguredError(
            "Mini-App CRM notes are not connected yet — upload the conversation as a file "
            "for now."
        )
