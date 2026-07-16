#!/usr/bin/env python3
"""D-06再現第2弾: シード後にrealtime音声を送る組み合わせ検証"""
import asyncio
import sys
sys.path.insert(0, "/opt/interview")

from google import genai
from google.genai import types
from app.config import load_settings
from app.live_bridge import build_live_connect_config, INPUT_MIME

settings = load_settings()
MODEL = "gemini-3.1-flash-live-preview"
# 16bit PCM 無音 0.5秒相当(16kHz想定)
SILENCE = b"\x00\x00" * 8000

PATTERNS = {
    "A1_シードtc_False→音声": (True, False),
    "A2_シードtc_True→音声": (True, True),
    "A3_シードなし→音声": (False, None),
}

async def drain(session, seconds):
    async def _inner():
        async for msg in session.receive():
            sc = getattr(msg, "server_content", None)
            print("    受信:", type(msg).__name__, "| turn_complete:", getattr(sc, "turn_complete", None) if sc else None)
    try:
        await asyncio.wait_for(_inner(), timeout=seconds)
    except asyncio.TimeoutError:
        pass

async def try_pattern(name, do_seed, tc):
    client = genai.Client(api_key=settings.gemini_api_key)
    config = build_live_connect_config(
        settings,
        system_instruction="あなたはテスト用アシスタントです。",
        voice_name="Puck",
    )
    try:
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            if do_seed:
                contents = [types.Content(role="model", parts=[types.Part(text="modelのテスト発話です。")])]
                await session.send_client_content(turns=contents, turn_complete=tc)
            await asyncio.sleep(1)
            await session.send_realtime_input(audio=types.Blob(data=SILENCE, mime_type=INPUT_MIME))
            await session.send_realtime_input(audio_stream_end=True)
            await drain(session, 10)
        print(f"[{name}] 結果: OK(1007なし)")
    except Exception as e:
        print(f"[{name}] 結果: NG -> {type(e).__name__}: {e}")

async def main():
    for name, (do_seed, tc) in PATTERNS.items():
        print(f"--- {name} ---")
        await try_pattern(name, do_seed, tc)
        await asyncio.sleep(1)

asyncio.run(main())
