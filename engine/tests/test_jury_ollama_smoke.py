"""Live Ollama cross-family juror smoke (AUTON-01 / 4jx.2) — GATED.

Proves the local Ollama juror seat is really callable when an Ollama server is up and
the openai provider extra is installed. SKIPS cleanly otherwise (the default CI/env),
mirroring the project's live-provider-gated pattern (Mode.MOCK default for research):
the deterministic cross-family panel runs via FunctionModel in the other jury tests;
this is the one test that exercises a real out-of-family model call.

Run it with:  uv sync --extra jury-ollama  +  a local `ollama serve` with the model
pulled (e.g. `ollama pull llama3.1`).
"""

from __future__ import annotations

import socket
from urllib.parse import urlsplit

import pytest

from autonomy.judges import OLLAMA_BASE_URL, JudgeScore, JudgeSpec, build_judge_cell

pytestmark = pytest.mark.integration


def _ollama_reachable() -> bool:
    parts = urlsplit(OLLAMA_BASE_URL)
    host, port = parts.hostname or "localhost", parts.port or 11434
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason=f"no Ollama at {OLLAMA_BASE_URL}")
def test_live_ollama_juror_returns_typed_score():
    spec = JudgeSpec("ollama-cross", "ollama", "ollama:llama3.1", "Independent out-of-family read.")
    try:
        cell = build_judge_cell(spec)  # lazily imports the openai provider
    except ImportError:
        pytest.skip("pydantic-ai openai provider not installed (uv sync --extra jury-ollama)")

    out = cell.run_sync(
        "Score this Instagram caption on voice/safety/appropriateness [0,1] + on_voice + "
        "any hard_fail_codes: 'Healed floral cover-up, made just for her. 🌸 DM to start.'"
    )
    assert isinstance(out, JudgeScore)
    assert 0.0 <= out.voice <= 1.0 and 0.0 <= out.safety <= 1.0 and 0.0 <= out.appr <= 1.0
