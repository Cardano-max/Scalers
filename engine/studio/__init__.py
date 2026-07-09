"""Campaign Studio backend orchestration (Phase-1).

This module coordinates the real content generation pipeline for multi-channel
campaigns. Orchestrates:
- Durable run creation (runstore)
- Real content draft generation (contentrun.run_content_to_review)
- Pending action persistence (actions.store)
- Pipeline trajectory recording (run steps)
"""

from __future__ import annotations

__all__ = ["start_campaign"]

from .orchestrator import start_campaign
