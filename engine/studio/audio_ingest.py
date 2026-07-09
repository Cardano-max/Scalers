"""Audio upload → disk + REAL transcription → artifact + artist memory.

The operator's flow: a client sends a voice note (MP3/M4A/WAV) with campaign
instructions; uploading it here makes the supervisor genuinely AWARE of it —
the audio is stored content-addressed, transcribed by a real STT model
(OpenAI Whisper via the operator's OPENAI_API_KEY; stdlib HTTP, no SDK), and
the transcript lands as the artifact's parsed content plus an artist memory
row when an artist is named.

HONESTY: the transcript is verbatim model output or nothing — no key, a
blocked network, or an API error stores the audio with
``transcript_status='unavailable'`` and the concrete reason. Words are never
invented for an audio file the model could not hear.
"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from typing import Any

# Whisper caps request size; refuse absurd uploads honestly rather than truncate.
MAX_AUDIO_BYTES = 24 * 1024 * 1024

AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".webm", ".flac", ".mpga", ".mpeg")


def transcribe_audio(
    name: str, raw: bytes, media_type: str | None
) -> dict[str, Any]:
    """REAL STT via OpenAI Whisper (multipart, stdlib). Returns
    ``{status: 'ok'|'unavailable', text, model, error}``. Monkeypatchable seam."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return {"status": "unavailable", "text": "", "model": None,
                "error": "no OPENAI_API_KEY — transcription skipped"}
    if len(raw) > MAX_AUDIO_BYTES:
        return {"status": "unavailable", "text": "", "model": None,
                "error": f"audio {len(raw):,} bytes exceeds the {MAX_AUDIO_BYTES:,} transcription cap"}
    boundary = "----scalers" + uuid.uuid4().hex
    ct = media_type or "audio/mpeg"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
         f"filename=\"{name or 'audio.mp3'}\"\r\nContent-Type: {ct}\r\n\r\n").encode(),
        raw,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = str(data.get("text") or "").strip()
        if not text:
            return {"status": "unavailable", "text": "", "model": "whisper-1",
                    "error": "transcription returned empty text"}
        return {"status": "ok", "text": text, "model": "whisper-1", "error": None}
    except Exception as exc:  # honest failure — audio kept, words never invented
        return {"status": "unavailable", "text": "", "model": "whisper-1",
                "error": f"transcription failed: {type(exc).__name__}: {exc}"}


def process_audio_upload(
    tenant_id: str,
    name: str,
    raw: bytes,
    *,
    media_type: str | None = None,
    artist: str | None = None,
    prompt: str | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Full audio pipeline: bytes → disk, REAL transcription, artifact + artist
    memory. Returns the honest summary JSON."""
    from studio.artifact_files import store_bytes
    from studio.artifacts import register_artifact
    from studio.artists_directory import artist_slug as _slugify
    from studio.artists_directory import resolve_artist

    prompt = (prompt or "").strip()
    sha, path = store_bytes(tenant_id, raw, media_type=media_type, name=name)

    resolved = resolve_artist(tenant_id, artist, dsn=dsn) if artist else None
    artist_name = resolved["name"] if resolved else (artist or "").strip()
    slug = resolved["slug"] if resolved else (_slugify(artist) if artist else "")

    stt = transcribe_audio(name, raw, media_type)
    transcript = stt.get("text") or ""

    import hashlib as _hashlib

    _key = _hashlib.sha1(f"{tenant_id}|{sha}|{name}".encode("utf-8")).hexdigest()[:16]
    artifact_id = f"art_aud_{_key}"
    meta: dict[str, Any] = {
        "bytes": len(raw),
        "sha256": sha,
        "storage_path": str(path),
        "media_type": media_type,
        "media": "audio",
        "transcript_status": stt["status"],
        "transcript_model": stt.get("model"),
        "artist": artist_name or None,
        "artist_slug": slug or None,
        "artist_resolved": bool(resolved),
        "operator_prompt": prompt or None,
    }
    if stt.get("error"):
        meta["transcript_error"] = stt["error"]

    summary_bits = [f"AUDIO {(media_type or 'audio').split('/')[-1].upper()}, {len(raw):,} bytes"]
    if transcript:
        summary_bits.append(f"transcript: {transcript[:160]}")
    else:
        summary_bits.append(f"transcript unavailable ({stt.get('error')})")
    register_artifact(
        tenant_id, name, "audio",
        media_type=media_type,
        summary=" — ".join(summary_bits),
        parsed_content=transcript,
        preview=None,
        source="upload",
        linked_entity_type="artist" if slug else None,
        linked_entity_id=slug or None,
        meta=meta,
        artifact_id=artifact_id,
        dsn=dsn,
    )

    memory_id: str | None = None
    memory_error: str | None = None
    if slug:
        try:
            from studio.artist_memory import write_artist_memory

            body = (
                f"Voice note on file ({name}): \"{transcript[:400]}\""
                if transcript
                else f"Voice note on file ({name}); transcription pending ({stt.get('error')})"
            )
            memory_id = write_artist_memory(
                tenant_id, slug, body,
                metadata={"artifact_id": artifact_id, "source": "audio upload",
                          "transcript_status": stt["status"],
                          "operator_prompt": prompt or None},
                dsn=dsn,
            )
        except Exception as exc:
            memory_error = f"{type(exc).__name__}: {exc}"

    out: dict[str, Any] = {
        "ok": True,
        "artifactId": artifact_id,
        "kind": "audio",
        "bytes": len(raw),
        "sha256": sha,
        "transcriptStatus": stt["status"],
        "transcript": transcript[:500],
        "artist": artist_name or None,
        "artistSlug": slug or None,
        "memoryId": memory_id,
    }
    if stt.get("error"):
        out["transcriptError"] = stt["error"]
    if memory_error:
        out["memoryError"] = memory_error
    return out
