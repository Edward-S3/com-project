#!/usr/bin/env python3
"""Gemini Live API connection smoke test (C-09 Step 1).

Connects to MODEL_GEMINI_LIVE, sends a short text (or PCM) prompt,
receives audio response, and reports interrupted / sessionResumption handling.

Exit 0 only when setup + audio (or transcript) response succeeds.
Does not proceed to P3 UI work until this passes.

Usage:
  source .venv/bin/activate
  python scripts/live_smoke.py
  python scripts/live_smoke.py --mode text
  python scripts/live_smoke.py --mode audio
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import struct
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google import genai
from google.genai import types

from app.config import load_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("live_smoke")


def _sine_pcm_16k(seconds: float = 0.4, freq: float = 440.0) -> bytes:
    """Generate short 16-bit LE PCM at 16 kHz (silent-ish tone for API accept)."""
    rate = 16000
    n = int(rate * seconds)
    samples = []
    for i in range(n):
        # low amplitude to avoid looking like speech content
        val = int(800 * __import__("math").sin(2 * 3.14159265 * freq * i / rate))
        samples.append(struct.pack("<h", val))
    return b"".join(samples)


async def run_smoke(*, mode: str, timeout_s: float) -> int:
    settings = load_settings()
    api_key = settings.gemini_api_key
    model = settings.model_gemini_live
    if not api_key:
        logger.error("GEMINI_API_KEY is not set in .env")
        return 2
    if not model:
        logger.error("MODEL_GEMINI_LIVE is not set in .env")
        return 2

    logger.info("model=%s mode=%s", model, mode)
    client = genai.Client(api_key=api_key)

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=(
            "あなたは接続テスト用の短い応答をするアシスタントです。"
            "1文だけ日本語で挨拶してください。"
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.MINIMAL
        ),
    )

    got_setup = False
    audio_bytes = 0
    out_transcript = ""
    in_transcript = ""
    interrupted = False
    resumption_handle: str | None = None
    resumption_updates = 0
    go_away = False
    usage_logged = False
    turn_complete = False

    deadline = time.monotonic() + timeout_s

    async with client.aio.live.connect(model=model, config=config) as session:
        got_setup = True
        logger.info("connected (setup complete)")

        if mode == "audio":
            pcm = _sine_pcm_16k(0.5)
            await session.send_realtime_input(
                audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
            )
            await session.send_realtime_input(audio_stream_end=True)
            # Also nudge with text so the model replies even if VAD ignores tone
            await session.send_realtime_input(text="こんにちは。短く返事してください。")
        else:
            await session.send_realtime_input(text="こんにちは。短く返事してください。")

        async def _consume() -> None:
            nonlocal got_setup, audio_bytes, out_transcript, in_transcript
            nonlocal interrupted, resumption_handle, resumption_updates
            nonlocal go_away, usage_logged, turn_complete
            async for message in session.receive():
                if message.setup_complete is not None:
                    got_setup = True

                if message.go_away is not None:
                    go_away = True
                    logger.info("go_away time_left=%s", message.go_away.time_left)

                if message.session_resumption_update is not None:
                    resumption_updates += 1
                    upd = message.session_resumption_update
                    if upd.new_handle:
                        resumption_handle = upd.new_handle
                    logger.info(
                        "sessionResumption update handle=%s resumable=%s",
                        bool(upd.new_handle),
                        upd.resumable,
                    )

                if message.usage_metadata is not None:
                    usage_logged = True
                    um = message.usage_metadata
                    logger.info(
                        "usage total=%s prompt=%s response=%s",
                        getattr(um, "total_token_count", None),
                        getattr(um, "prompt_token_count", None),
                        getattr(um, "response_token_count", None),
                    )

                sc = message.server_content
                if sc is None:
                    continue

                if sc.interrupted:
                    interrupted = True
                    logger.info("interrupted=true (barge-in signal)")

                if sc.input_transcription and sc.input_transcription.text:
                    in_transcript += sc.input_transcription.text

                if sc.output_transcription and sc.output_transcription.text:
                    out_transcript += sc.output_transcription.text

                if sc.model_turn and sc.model_turn.parts:
                    for part in sc.model_turn.parts:
                        inline = getattr(part, "inline_data", None)
                        if inline is not None and inline.data:
                            audio_bytes += len(inline.data)

                if sc.turn_complete:
                    turn_complete = True
                    logger.info("turn_complete")
                    return

        try:
            await asyncio.wait_for(_consume(), timeout=max(1.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            logger.warning("timed out waiting for turn_complete")

    ok = got_setup and (audio_bytes > 0 or bool(out_transcript.strip()))
    print("--- live_smoke result ---")
    print(f"model: {model}")
    print(f"setup: {got_setup}")
    print(f"audio_bytes: {audio_bytes}")
    print(f"output_transcript: {out_transcript!r}")
    print(f"input_transcript: {in_transcript!r}")
    print(f"interrupted_seen: {interrupted}")
    print(f"session_resumption_updates: {resumption_updates}")
    print(f"session_resumption_handle: {bool(resumption_handle)}")
    print(f"go_away_seen: {go_away}")
    print(f"usage_metadata: {usage_logged}")
    print(f"turn_complete: {turn_complete}")
    print(f"PASS: {ok}")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini Live API smoke test")
    parser.add_argument(
        "--mode",
        choices=("text", "audio"),
        default="text",
        help="Send text realtime input (default) or PCM+text",
    )
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()
    try:
        code = asyncio.run(run_smoke(mode=args.mode, timeout_s=args.timeout))
    except Exception:
        logger.exception("smoke test failed with exception")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
