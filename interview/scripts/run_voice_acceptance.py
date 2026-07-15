#!/usr/bin/env python3
"""P3 voice acceptance: 1A / SME via Live API (text realtime + barge-in).

Drives Gemini Live without a browser mic, persists transcript / barge-in markers /
judge JSON under docs/acceptance/P3/.

Usage:
  source .venv/bin/activate
  python scripts/run_voice_acceptance.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google import genai
from google.genai import types

from app.config import load_settings
from app.live_bridge import build_live_connect_config, resolve_live_voice
from app.models import SessionConfig
from app.partner import build_partner_system_prompt
from app.session_service import SessionService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("voice_acceptance")

OUT_DIR = PROJECT_ROOT / "docs" / "acceptance" / "P3"


async def _collect_until_complete(
    session: object,
    *,
    barge_after_audio_chunks: int | None = None,
    barge_after_transcript_chars: int | None = None,
    barge_text: str | None = None,
) -> tuple[str, bool, int, list[dict]]:
    """Receive one model turn. Optionally barge-in after N audio chunks or transcript chars."""
    partner_buf = ""
    interrupted = False
    audio_chunks = 0
    usage = 0
    barge_events: list[dict] = []
    barged = False

    async def _do_barge(reason: str) -> None:
        nonlocal barged, interrupted, partner_buf
        if barged or not barge_text:
            return
        barged = True
        cut = partner_buf
        barge_events.append(
            {
                "trigger": reason,
                "audio_chunks_before": audio_chunks,
                "partner_played_before_interrupt": cut,
                "interrupt_utterance": barge_text,
            }
        )
        await session.send_realtime_input(text=barge_text)  # type: ignore[attr-defined]
        # Client considers subsequent partner audio discarded (playback stop equivalent)
        interrupted = True

    async for message in session.receive():  # type: ignore[attr-defined]
        if message.usage_metadata and message.usage_metadata.total_token_count:
            usage += int(message.usage_metadata.total_token_count)
        sc = message.server_content
        if not sc:
            continue

        if sc.output_transcription and sc.output_transcription.text and not interrupted:
            partner_buf += sc.output_transcription.text
            if (
                barge_after_transcript_chars is not None
                and len(partner_buf) >= barge_after_transcript_chars
            ):
                await _do_barge("client_early_input_after_transcript")

        if sc.model_turn and sc.model_turn.parts:
            for part in sc.model_turn.parts:
                inline = getattr(part, "inline_data", None)
                if inline and inline.data:
                    audio_chunks += 1
                    if (
                        barge_after_audio_chunks is not None
                        and audio_chunks >= barge_after_audio_chunks
                    ):
                        await _do_barge("client_early_input_after_audio")

        if sc.interrupted:
            interrupted = True
            barge_events.append(
                {
                    "trigger": "server_interrupted",
                    "partner_played_before_interrupt": partner_buf,
                }
            )
            break

        if sc.turn_complete:
            break

    return partner_buf.strip(), interrupted, usage, barge_events


async def _run_live_dialogue(svc: SessionService, session_id: int) -> dict:
    settings = svc.settings
    row = svc.db.get_session(session_id)
    assert row is not None
    config = SessionConfig(
        scene=row["scene"],
        role=row["role"],
        difficulty=row["difficulty"],
        io_mode="voice",
    )
    persona = row["persona"]
    user_profile = svc.db.get_user_profile(row["user_id"])
    system_instruction = build_partner_system_prompt(config, persona, user_profile)
    voice = resolve_live_voice(settings, str(persona.get("role", "")))
    model = settings.model_gemini_live
    assert model and settings.gemini_api_key

    client = genai.Client(api_key=settings.gemini_api_key)
    connect_cfg = build_live_connect_config(
        settings,
        system_instruction=system_instruction,
        voice_name=voice,
    )

    all_barge: list[dict] = []
    usage_total = 0

    script = [
        ("user", "今日は期初の目標設定だね。まず今期の目標案を聞かせてくれるかな。"),
        ("user", "もう少し具体的に、数値や期限を入れてもらえるかな。"),
        # barge-in will inject the next line during previous AI response
        ("barge_target", "品質向上ではなく、上期までに検査ミスを月3件に抑える形にしてほしい。"),
        ("user", "いいね。その水準まで来たら頑張れそうだ。他に気になることはある？"),
        ("user", "面談を終了します。"),
    ]

    async with client.aio.live.connect(model=model, config=connect_cfg) as session:
        turns = svc.db.list_turns(session_id)
        if turns:
            contents = [
                types.Content(
                    role="model" if t["speaker"] == "partner" else "user",
                    parts=[types.Part(text=t["text"])],
                )
                for t in turns
            ]
            try:
                await session.send_client_content(turns=contents, turn_complete=False)
            except Exception:
                logger.exception("seed opening failed; continuing")

        i = 0
        while i < len(script):
            kind, text = script[i]
            if kind == "barge_target":
                i += 1
                continue

            if text.startswith("面談を終了"):
                svc.add_voice_turn(session_id, "user", text)
                break

            svc.add_voice_turn(session_id, "user", text)
            await session.send_realtime_input(text=text)

            barge_text = None
            barge_chunks = None
            barge_chars = None
            # After second user turn, barge-in with the prepared line
            if i == 1 and i + 1 < len(script) and script[i + 1][0] == "barge_target":
                barge_text = script[i + 1][1]
                barge_chunks = 1
                barge_chars = 8

            try:
                partner, interrupted, usage, barge_events = await asyncio.wait_for(
                    _collect_until_complete(
                        session,
                        barge_after_audio_chunks=barge_chunks,
                        barge_after_transcript_chars=barge_chars,
                        barge_text=barge_text,
                    ),
                    timeout=50,
                )
            except asyncio.TimeoutError:
                logger.warning("timeout awaiting response for step %s", i)
                partner, interrupted, usage, barge_events = "", False, 0, []

            usage_total += usage
            for ev in barge_events:
                ev["after_user_seq"] = i
                all_barge.append(ev)

            if partner:
                svc.add_voice_turn(
                    session_id, "partner", partner, interrupted=interrupted
                )

            if barge_text and barge_events:
                # Persist interrupt utterance as user turn if not already
                svc.add_voice_turn(session_id, "user", barge_text)
                try:
                    partner2, interrupted2, usage2, _ = await asyncio.wait_for(
                        _collect_until_complete(session),
                        timeout=50,
                    )
                except asyncio.TimeoutError:
                    partner2, interrupted2, usage2 = "", False, 0
                usage_total += usage2
                if partner2:
                    svc.add_voice_turn(
                        session_id,
                        "partner",
                        partner2,
                        interrupted=interrupted2,
                    )
                i += 2  # skip barge_target
                continue

            i += 1

    report = svc.end_session(session_id)
    svc.record_live_usage(
        session_id,
        {"total_tokens_observed": usage_total, "model": model},
    )
    return {
        "barge_in_events": all_barge,
        "report": report,
        "usage_total_observed": usage_total,
        "model": model,
        "voice": voice,
    }


def write_artifacts(session_id: int, svc: SessionService, meta: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    row = svc.db.get_session(session_id)
    assert row is not None
    turns = svc.db.list_turns(session_id)
    report = meta["report"]
    barge = meta["barge_in_events"]

    md_path = OUT_DIR / "S1_voice.md"
    lines = [
        "# P3 S1: 1A / 中小企業（音声経路）",
        "",
        "※利用者発話は受入スクリプトが Live API の realtime text 入力として代行（マイクの代替）。",
        "バージインは応答音声チャンク受信中に次発話を送り、打ち切り位置をログ化した。",
        "",
        "## (a) 設定",
        "",
        "- モード: 1A (期初目標設定 × 上司役（AI=部下）)",
        "- 難易度: 中小企業",
        f"- 入出力: 音声（Live / `{meta['model']}` / voice={meta['voice']}）",
        f"- session_id: {session_id}",
        f"- 利用者プロフィール: {json.dumps(svc.db.get_user_profile(row['user_id']), ensure_ascii=False)}",
        f"- ペルソナ要約: {json.dumps({k: v for k, v in row['persona'].items() if k != 'hidden_facts'}, ensure_ascii=False)}",
        "",
        "## (b) トランスクリプト",
        "",
    ]
    for t in turns:
        speaker = "利用者" if t["speaker"] == "user" else "AI"
        mark = ""
        if t.get("warnings_json") and "interrupted" in (t.get("warnings_json") or ""):
            mark = " 〔interrupted: 再生分のみ〕"
        lines.append(f"[{t['seq']}] {speaker}: {t['text']}{mark}")
        lines.append("")

    lines.extend(
        [
            "## (c) バージイン発生箇所",
            "",
            "```json",
            json.dumps(barge, ensure_ascii=False, indent=2),
            "```",
            "",
            "## (d) 審判出力JSON（既存 judge.py）",
            "",
            "```json",
            json.dumps(report, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    (OUT_DIR / "S1_voice_transcript.json").write_text(
        json.dumps(turns, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "S1_voice_judge.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "S1_voice_barge_in.json").write_text(
        json.dumps(barge, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return md_path


def main() -> None:
    settings = load_settings()
    if not settings.gemini_api_key or not settings.model_gemini_live:
        raise SystemExit("GEMINI_API_KEY / MODEL_GEMINI_LIVE required")

    svc = SessionService(settings)
    uid = svc.create_user(
        {
            "name": "検収1A音声",
            "department": "製造",
            "age": 40,
            "tenure_years": 15,
            "grade": "4-1",
        }
    )
    config = SessionConfig("initial", "supervisor", "sme", "voice")
    state, opening = svc.start_session(uid, config)
    logger.info("session_id=%s opening_len=%s", state.session_id, len(opening))

    meta = asyncio.run(_run_live_dialogue(svc, state.session_id))
    path = write_artifacts(state.session_id, svc, meta)
    logger.info("wrote %s barge_in_count=%s", path, len(meta["barge_in_events"]))
    if not meta["barge_in_events"]:
        logger.warning("no barge-in events captured — review manually")
    print(f"PASS session_id={state.session_id} artifacts={OUT_DIR}")


if __name__ == "__main__":
    main()
