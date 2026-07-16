#!/usr/bin/env python3
"""D-06最小再現: 履歴シードのパターン別に1007発生を切り分け(DB書き込みなし)"""
import asyncio
import sys
sys.path.insert(0, "/opt/interview")

from google import genai
from google.genai import types
from app.config import load_settings
from app.live_bridge import build_live_connect_config

settings = load_settings()
MODEL = "gemini-3.1-flash-live-preview"

PATTERNS = {
    "P1_model単独_tc_False": (["model"], False),
    "P2_model単独_tc_True": (["model"], True),
    "P3_user先頭_tc_False": (["user", "model"], False),
    "P4_user先頭_tc_True": (["user", "model"], True),
}

async def receive_one(session):
    async for msg in session.receive():
        return type(msg).__name__
    return "stream終了"

async def try_pattern(name, roles, turn_complete):
    client = genai.Client(api_key=settings.gemini_api_key)
    config = build_live_connect_config(
        settings,
        system_instruction="あなたはテスト用アシスタントです。",
        voice_name="Puck",
    )
    contents = [
        types.Content(role=r, parts=[types.Part(text=f"{r}のテスト発話です。")])
        for r in roles
    ]
    try:
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            await session.send_client_content(turns=contents, turn_complete=turn_complete)
            try:
                got = await asyncio.wait_for(receive_one(session), timeout=8)
                print(f"[{name}] 受信OK: {got}")
            except asyncio.TimeoutError:
                print(f"[{name}] 8秒間エラーなし(受信なしだが切断もなし)")
        print(f"[{name}] 結果: OK")
    except Exception as e:
        print(f"[{name}] 結果: NG -> {type(e).__name__}: {e}")

async def main():
    for name, (roles, tc) in PATTERNS.items():
        await try_pattern(name, roles, tc)
        await asyncio.sleep(1)

asyncio.run(main())
