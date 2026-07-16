#!/usr/bin/env python3
"""D-06完全再現: session 14の実データ(実プロンプト+実シード)で検証"""
import asyncio
import sys
sys.path.insert(0, "/opt/interview")

from google import genai
from google.genai import types
from app.config import load_settings
from app.db import Database
from app.live_bridge import build_live_connect_config, resolve_live_voice, INPUT_MIME
from app.models import SessionConfig
from app.partner import build_partner_system_prompt

settings = load_settings()
MODEL = "gemini-3.1-flash-live-preview"
SILENCE = b"\x00\x00" * 8000

db = Database(settings.db_path)
row = db.get_session(14)
config = SessionConfig(row["scene"], row["role"], row["difficulty"], "voice")
persona = row["persona"]
user_profile = db.get_user_profile(row["user_id"])
system_instruction = build_partner_system_prompt(config, persona, user_profile)
voice_name = resolve_live_voice(settings, str(persona.get("role", "")))
turns = db.list_turns(14)
contents = [
    types.Content(
        role="model" if t["speaker"] == "partner" else "user",
        parts=[types.Part(text=t["text"])],
    )
    for t in turns
]
print(f"system_instruction長: {len(system_instruction)}字 / シード: {len(contents)}件 / voice: {voice_name}")

async def main():
    client = genai.Client(api_key=settings.gemini_api_key)
    cfg = build_live_connect_config(
        settings,
        system_instruction=system_instruction,
        voice_name=voice_name,
    )
    try:
        async with client.aio.live.connect(model=MODEL, config=cfg) as session:
            await session.send_client_content(turns=contents, turn_complete=False)
            print("シード送信OK、45秒待機後に音声送信(実挙動の再現)")
            await asyncio.sleep(45)
            await session.send_realtime_input(audio=types.Blob(data=SILENCE, mime_type=INPUT_MIME))
            await session.send_realtime_input(audio_stream_end=True)
            async def _drain():
                async for msg in session.receive():
                    print("受信:", type(msg).__name__)
            try:
                await asyncio.wait_for(_drain(), timeout=15)
            except asyncio.TimeoutError:
                pass
        print("結果: OK(1007なし)")
    except Exception as e:
        print(f"結果: NG -> {type(e).__name__}: {e}")

asyncio.run(main())
