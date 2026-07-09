"""Video ingest: honest frame-merge + honest degradation (no ffmpeg / no VLM).
The end-to-end path (real mp4 → 5 frames → artifact/b-roll/memory rows) was
verified live; these pin the pure pieces CI can run without media tooling."""

from __future__ import annotations

from studio import video_ingest
from studio.video_ingest import _merge_frame_analyses, extract_frames


def _ok(ts_tags: dict, summary: str = "", facts: str = "x") -> dict:
    return {"status": "ok", "tags": ts_tags, "facts_text": facts,
            "summary": summary, "model": "m", "fact_count": 1, "error": None}


def test_merge_unions_tags_and_keeps_timestamped_facts():
    merged = _merge_frame_analyses([
        (1.0, _ok({"styles": ["realism"], "motifs": ["lion"], "color_mode": "color",
                   "mood": "bold", "complexity": "high", "campaign_fit": ["a"]},
                  summary="frame one")),
        (9.0, _ok({"styles": ["realism", "fine-line"], "motifs": ["rose"],
                   "color_mode": "color", "mood": "bold", "complexity": "high",
                   "campaign_fit": ["b"]})),
    ])
    assert merged["status"] == "ok"
    assert merged["tags"]["styles"] == ["realism", "fine-line"]  # order-preserving union
    assert merged["tags"]["motifs"] == ["lion", "rose"]
    assert merged["tags"]["color_mode"] == "color"
    assert merged["tags"]["campaign_fit"] == ["a", "b"]
    assert merged["summary"] == "frame one"
    # Facts keep their frame timestamps — traceable, never blended away.
    assert "[t=1.0s]" in merged["facts_text"] and "[t=9.0s]" in merged["facts_text"]


def test_merge_all_unavailable_is_honest():
    bad = {"status": "unavailable", "tags": {}, "facts_text": "", "summary": "",
           "model": None, "fact_count": 0, "error": "no ANTHROPIC_API_KEY"}
    merged = _merge_frame_analyses([(0.0, bad), (5.0, bad)])
    assert merged["status"] == "unavailable"
    assert merged["error"] == "no ANTHROPIC_API_KEY"
    assert merged["tags"] == {} and merged["fact_count"] == 0


def test_extract_frames_without_ffmpeg_degrades_honestly(monkeypatch):
    monkeypatch.setattr(video_ingest, "_ffmpeg_exe", lambda: None)
    frames, reason = extract_frames(b"not-a-video")
    assert frames == []
    assert "imageio-ffmpeg" in (reason or "")
