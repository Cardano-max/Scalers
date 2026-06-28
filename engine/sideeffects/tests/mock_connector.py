"""A mock side-effect connector for the exactly-once test (Phase 1).

Stands in for the real Meta/Gmail MCP connector that lands in Phase 6. Its only
job here is to record every invocation so a test can assert the connector was
called exactly once.
"""

from __future__ import annotations


class MockConnector:
    def __init__(self) -> None:
        self.calls: list[str] = []  # every key we were asked to send

    async def send(self, key: str, channel: str, payload: dict) -> str:
        self.calls.append(key)
        return f"prov-{len(self.calls)}"

    @property
    def call_count(self) -> int:
        return len(self.calls)
