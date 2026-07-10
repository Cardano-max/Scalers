"""Video upload → disk + REAL frame-sampled visual understanding.

Videos go through the SAME honesty pipeline as images (studio/image_ingest.py),
with one added step: the model cannot watch a video directly, so we sample a
handful of evenly-spaced REAL frames (bundled static ffmpeg via imageio-ffmpeg —
no system install) and run the citation-gated VLM on each frame. The stored tags
are the union of what was actually seen on those frames, each frame's facts kept
with its timestamp — nothing is ever summarized from imagination.

HONEST DEGRADATION mirrors the image path: no ffmpeg → the video still persists
(``frames_analyzed=0``, reason recorded); no model key / VLM failure → frames
persist as extraction proof but ``vlm_status='unavailable'`` with the concrete
error. Library rows carry ``media='video'`` so the IG pipeline can treat these
as B-ROLL candidates distinct from still artwork.
"""

from __future__ import annotations

import base64
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Bounded work per upload: a 30-minute reel must not become 1,800 VLM calls.
MAX_FRAMES = 5
_FRAME_POSITIONS = (0.08, 0.28, 0.5, 0.72, 0.92)  # fractions of duration
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def probe_duration_seconds(path: str, *, ffmpeg: str | None = None) -> float | None:
    """Real container duration parsed from ffmpeg's own stream info; None when
    unreadable (never guessed)."""
    exe = ffmpeg or _ffmpeg_exe()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-i", path],
            capture_output=True, text=True, timeout=60,
        )
        m = _DURATION_RE.search(proc.stderr or "")
        if not m:
            return None
        h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mnt * 60 + s
    except Exception:
        return None


def extract_frames(raw: bytes, *, max_frames: int = MAX_FRAMES) -> tuple[list[tuple[float, bytes]], str | None]:
    """Evenly-spaced REAL frames as ``[(timestamp_s, jpeg_bytes), ...]`` plus an
    honest reason when extraction was impossible. Bounded to ``max_frames``."""
    exe = _ffmpeg_exe()
    if not exe:
        return [], "imageio-ffmpeg not installed — frame extraction skipped"
    with tempfile.NamedTemporaryFile(suffix=".video", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        duration = probe_duration_seconds(tmp_path, ffmpeg=exe)
        if duration is None or duration <= 0:
            stamps = [0.0]
        else:
            stamps = [round(duration * f, 2) for f in _FRAME_POSITIONS[:max_frames]]
        frames: list[tuple[float, bytes]] = []
        for ts in stamps:
            try:
                proc = subprocess.run(
                    [exe, "-hide_banner", "-loglevel", "error",
                     "-ss", str(ts), "-i", tmp_path,
                     "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                    capture_output=True, timeout=120,
                )
                if proc.returncode == 0 and proc.stdout:
                    frames.append((ts, proc.stdout))
            except Exception:
                continue  # one bad seek must not lose the other frames
        if not frames:
            return [], "ffmpeg produced no frames (unreadable/unsupported codec)"
        return frames, None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _merge_frame_analyses(analyses: list[tuple[float, dict[str, Any]]]) -> dict[str, Any]:
    """Union of what was REALLY seen across frames. Tags stay traceable: each
    frame's facts are kept under its timestamp in ``facts_text``."""
    ok = [(ts, a) for ts, a in analyses if a.get("status") == "ok"]
    if not ok:
        first_err = next((a.get("error") for _, a in analyses if a.get("error")), None)
        return {
            "status": "unavailable" if first_err else "no_facts",
            "tags": {}, "facts_text": "", "summary": "",
            "model": next((a.get("model") for _, a in analyses if a.get("model")), None),
            "fact_count": 0, "error": first_err,
        }

    def _union(key: str) -> list[str]:
        seen: dict[str, None] = {}
        for _, a in ok:
            for v in (a.get("tags") or {}).get(key) or []:
                seen.setdefault(v, None)
        return list(seen)

    color_modes = [str((a.get("tags") or {}).get("color_mode") or "") for _, a in ok]
    color_modes = [c for c in color_modes if c]
    tags = {
        "styles": _union("styles"),
        "motifs": _union("motifs"),
        "color_mode": max(set(color_modes), key=color_modes.count) if color_modes else "",
        "mood": ", ".join(_union("mood")) if isinstance((ok[0][1].get("tags") or {}).get("mood"), list)
                 else next((str((a.get("tags") or {}).get("mood") or "") for _, a in ok if (a.get("tags") or {}).get("mood")), ""),
        "complexity": next((str((a.get("tags") or {}).get("complexity") or "") for _, a in ok if (a.get("tags") or {}).get("complexity")), ""),
        "campaign_fit": _union("campaign_fit"),
    }
    facts_lines = [
        f"[t={ts:.1f}s] {a.get('facts_text', '').strip()}"
        for ts, a in ok if a.get("facts_text")
    ]
    summaries = [a.get("summary") for _, a in ok if a.get("summary")]
    return {
        "status": "ok",
        "tags": tags,
        "facts_text": "\n".join(facts_lines),
        "summary": summaries[0] if summaries else "",
        "model": ok[0][1].get("model"),
        "fact_count": sum(a.get("fact_count") or 0 for _, a in ok),
        "error": None,
    }


def process_video_upload(
    tenant_id: str,
    name: str,
    raw: bytes,
    *,
    media_type: str | None = None,
    artist: str | None = None,
    prompt: str | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Full video pipeline: bytes → disk, frames → REAL VLM, artifact + b-roll
    library row + artist memory. Returns the honest summary JSON (frame counts,
    per-step errors — never a claimed success that didn't happen)."""
    from studio.artifact_files import store_bytes
    from studio.artifacts import register_artifact
    from studio.artists_directory import artist_slug as _slugify
    from studio.artists_directory import resolve_artist
    from studio.image_ingest import _resolve_dsn, analyze_image

    prompt = (prompt or "").strip()
    sha, path = store_bytes(tenant_id, raw, media_type=media_type, name=name)

    resolved = resolve_artist(tenant_id, artist, dsn=dsn) if artist else None
    artist_name = resolved["name"] if resolved else (artist or "").strip()
    slug = resolved["slug"] if resolved else (_slugify(artist) if artist else "")

    duration = None
    frames, frame_error = extract_frames(raw)
    if frames:
        # duration probe already ran inside extract_frames; re-probe cheaply for meta
        with tempfile.NamedTemporaryFile(suffix=".video", delete=False) as tmp:
            tmp.write(raw)
        try:
            duration = probe_duration_seconds(tmp.name)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    analyses = [(ts, analyze_image(tenant_id, f"{name}@{ts:.1f}s", jpg, "image/jpeg"))
                for ts, jpg in frames]
    vlm = _merge_frame_analyses(analyses)
    tags = vlm.get("tags") or {}

    # Poster frame as the preview (bounded like image thumbnails).
    from studio.image_ingest import THUMBNAIL_MAX_CHARS

    preview = None
    if frames:
        poster_uri = "data:image/jpeg;base64," + base64.standard_b64encode(frames[0][1]).decode()
        preview = poster_uri if len(poster_uri) <= THUMBNAIL_MAX_CHARS else None

    import hashlib as _hashlib

    _key = _hashlib.sha1(f"{tenant_id}|{sha}|{name}".encode("utf-8")).hexdigest()[:16]
    artifact_id = f"art_vid_{_key}"
    meta: dict[str, Any] = {
        "bytes": len(raw),
        "sha256": sha,
        "storage_path": str(path),
        "media_type": media_type,
        "media": "video",
        "video": {
            "duration_s": duration,
            "frames_extracted": len(frames),
            "frames_analyzed": sum(1 for _, a in analyses if a.get("status") == "ok"),
            "frame_timestamps": [ts for ts, _ in frames],
        },
        "vlm_status": vlm["status"],
        "vlm_model": vlm.get("model"),
        "artist": artist_name or None,
        "artist_slug": slug or None,
        "artist_resolved": bool(resolved),
        "operator_prompt": prompt or None,
    }
    if frame_error:
        meta["frame_error"] = frame_error
    if vlm.get("error"):
        meta["vlm_error"] = vlm["error"]
    if vlm["status"] == "ok":
        meta["vlm"] = {
            "styles": tags.get("styles", []),
            "motifs": tags.get("motifs", []),
            "color_mode": tags.get("color_mode", ""),
            "mood": tags.get("mood", ""),
            "complexity": tags.get("complexity", ""),
            "campaign_fit": tags.get("campaign_fit", []),
        }

    dur_bit = f", {duration:.0f}s" if duration else ""
    summary_bits = [f"VIDEO {(media_type or 'video').split('/')[-1].upper()}, {len(raw):,} bytes{dur_bit}, "
                    f"{len(frames)} frame(s) sampled"]
    if vlm.get("summary"):
        summary_bits.append(vlm["summary"])
    register_artifact(
        tenant_id, name, "video",
        media_type=media_type,
        summary=" — ".join(summary_bits),
        parsed_content=vlm.get("facts_text") or "",
        preview=preview,
        source="upload",
        linked_entity_type="artist" if slug else None,
        linked_entity_id=slug or None,
        meta=meta,
        artifact_id=artifact_id,
        dsn=dsn,
    )

    asset_id: str | None = None
    asset_error: str | None = None
    try:
        from team.store import TeamStore

        from studio.artwork_select import (
            ARTWORK_ASSET_TYPE,
            ARTWORK_STATUS,
            _portfolio_campaign_id,
        )

        store = TeamStore(_resolve_dsn(dsn))
        store.setup()
        asset_id = f"art_upload_{tenant_id}_{_key}"
        store.record_asset(
            id=asset_id,
            campaign_id=_portfolio_campaign_id(tenant_id),
            asset_type=ARTWORK_ASSET_TYPE,
            content={
                "artist": artist_name,
                "image_ref": f"artifact://{artifact_id}",
                "caption": (vlm.get("summary") or prompt or name or "").strip(),
                "styles": tags.get("styles", []),
                "motifs": tags.get("motifs", []),
                "collection": "",
                "is_best_example": False,
                "source": "upload",
                "media": "video",  # b-roll candidate, not a still attachment
                "vlm_summary": vlm.get("summary") or "",
                "vlm_status": vlm["status"],
                "color_mode": tags.get("color_mode", ""),
                "mood": tags.get("mood", ""),
                "complexity": tags.get("complexity", ""),
                "campaign_fit": tags.get("campaign_fit", []),
                "artifact_id": artifact_id,
            },
            status=ARTWORK_STATUS,
        )
    except Exception as exc:
        asset_id = None
        asset_error = f"{type(exc).__name__}: {exc}"

    memory_id: str | None = None
    memory_error: str | None = None
    if slug:
        try:
            from studio.artist_memory import write_artist_memory

            desc = vlm.get("summary") or "frames stored; visual analysis pending (see vlm_status)"
            memory_id = write_artist_memory(
                tenant_id, slug,
                f"New VIDEO uploaded ({name}{dur_bit}, {len(frames)} frames sampled): {desc}",
                metadata={"artifact_id": artifact_id, "source": "video upload",
                          "vlm_status": vlm["status"], "operator_prompt": prompt or None},
                dsn=dsn,
            )
        except Exception as exc:
            memory_error = f"{type(exc).__name__}: {exc}"

    out: dict[str, Any] = {
        "ok": True,
        "artifactId": artifact_id,
        "kind": "video",
        "bytes": len(raw),
        "sha256": sha,
        "durationSeconds": duration,
        "framesExtracted": len(frames),
        "framesAnalyzed": sum(1 for _, a in analyses if a.get("status") == "ok"),
        "vlmStatus": vlm["status"],
        # Top-level summary for the console ack — without it a fully analyzed
        # video acked as a bare "Uploaded." (a real operator saw this and
        # concluded video analysis was broken).
        "vlmSummary": vlm.get("summary") or None,
        "artist": artist_name or None,
        "artistSlug": slug or None,
        "assetId": asset_id,
        "memoryId": memory_id,
    }
    if frame_error:
        out["frameError"] = frame_error
    if vlm.get("error"):
        out["vlmError"] = vlm["error"]
    if asset_error:
        out["assetError"] = asset_error
    if memory_error:
        out["memoryError"] = memory_error
    return out
