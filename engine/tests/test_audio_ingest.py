"""Audio ingest honesty: transcription unavailable without a key; the seam is
monkeypatchable so the artifact/memory writes are covered elsewhere via route
tests. (Live Whisper verified on the operator's machine — sandbox blocks it.)"""

from __future__ import annotations

from studio.audio_ingest import MAX_AUDIO_BYTES, transcribe_audio


def test_no_key_is_honest(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = transcribe_audio("note.mp3", b"abc", "audio/mpeg")
    assert out["status"] == "unavailable"
    assert "OPENAI_API_KEY" in out["error"]
    assert out["text"] == ""


def test_oversize_is_refused_not_truncated(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    out = transcribe_audio("big.mp3", b"x" * (MAX_AUDIO_BYTES + 1), "audio/mpeg")
    assert out["status"] == "unavailable"
    assert "cap" in out["error"]
