"""Gemini Live API WebSocket bridge (browser ↔ FastAPI ↔ Live).

API keys never leave the server. Model name comes from MODEL_GEMINI_LIVE.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from app.checker import check_text, format_warnings
from app.config import Settings, load_settings
from app.models import SessionConfig
from app.partner import build_partner_system_prompt
from app.session_service import SessionService

logger = logging.getLogger(__name__)

INPUT_MIME = "audio/pcm;rate=16000"
OUTPUT_RATE_HZ = 24000


def resolve_live_voice(settings: Settings, persona_role: str) -> str:
    """AI persona role → prebuilt voice (.env overrideable)."""
    if persona_role == "subordinate":
        return settings.voice_subordinate
    return settings.voice_supervisor


def build_live_connect_config(
    settings: Settings,
    *,
    system_instruction: str,
    voice_name: str,
    resumption_handle: str | None = None,
) -> types.LiveConnectConfig:
    """Assemble Live setup; model itself is passed to connect(), not hardcoded here."""
    session_resumption = types.SessionResumptionConfig()
    if resumption_handle:
        session_resumption = types.SessionResumptionConfig(handle=resumption_handle)

    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=system_instruction,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=session_resumption,
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.MINIMAL
        ),
        history_config=types.HistoryConfig(initial_history_in_client_content=True),
    )


@dataclass
class UsageAccumulator:
    prompt_tokens: int = 0
    response_tokens: int = 0
    total_tokens: int = 0
    events: int = 0

    def add(self, meta: Any) -> dict[str, int]:
        prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
        response = int(getattr(meta, "response_token_count", 0) or 0)
        total = int(getattr(meta, "total_token_count", 0) or 0)
        self.prompt_tokens += prompt
        self.response_tokens += response
        self.total_tokens += total
        self.events += 1
        return {
            "prompt_tokens": prompt,
            "response_tokens": response,
            "total_tokens": total,
            "session_prompt_tokens": self.prompt_tokens,
            "session_response_tokens": self.response_tokens,
            "session_total_tokens": self.total_tokens,
        }

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "response_tokens": self.response_tokens,
            "total_tokens": self.total_tokens,
            "events": self.events,
        }


@dataclass
class TranscriptBuffer:
    user_text: str = ""
    partner_text: str = ""
    partner_interrupted: bool = False

    def clear_partner(self) -> None:
        self.partner_text = ""
        self.partner_interrupted = False


@dataclass
class LiveSessionContext:
    session_id: int
    config: SessionConfig
    persona: dict[str, Any]
    user_profile: dict[str, Any]
    system_instruction: str
    voice_name: str
    usage: UsageAccumulator = field(default_factory=UsageAccumulator)
    buffers: TranscriptBuffer = field(default_factory=TranscriptBuffer)
    resumption_handle: str | None = None
    closed: bool = False


class LiveBridge:
    """Server-side proxy for one browser WebSocket session."""

    def __init__(
        self,
        websocket: WebSocket,
        session_id: int,
        *,
        settings: Settings | None = None,
        service: SessionService | None = None,
    ) -> None:
        self.websocket = websocket
        self.session_id = session_id
        self.settings = settings or load_settings()
        self.service = service or SessionService(self.settings)
        self.ctx: LiveSessionContext | None = None
        self._gemini_session: Any = None

    async def run(self) -> None:
        await self.websocket.accept()
        try:
            self.ctx = self._load_context()
        except ValueError as exc:
            await self._send({"type": "error", "message": str(exc)})
            await self.websocket.close()
            return

        if not self.settings.gemini_api_key:
            await self._send({"type": "error", "message": "音声APIの設定がありません。"})
            await self.websocket.close()
            return
        if not self.settings.model_gemini_live:
            await self._send(
                {
                    "type": "error",
                    "message": "MODEL_GEMINI_LIVE が未設定です。",
                }
            )
            await self.websocket.close()
            return

        await self._send(
            {
                "type": "status",
                "status": "connecting",
                "model": self.settings.model_gemini_live,
                "voice": self.ctx.voice_name,
            }
        )

        client = genai.Client(api_key=self.settings.gemini_api_key)
        try:
            await self._session_loop(client)
        except WebSocketDisconnect:
            logger.info("browser disconnected session_id=%s", self.session_id)
        except Exception:
            logger.exception("live bridge error session_id=%s", self.session_id)
            try:
                await self._send(
                    {
                        "type": "error",
                        "message": "音声接続でエラーが発生しました。再試行してください。",
                    }
                )
            except Exception:
                pass
        finally:
            await self._flush_buffers(force=True)
            if self.ctx:
                logger.info(
                    "session_id=%s live usage %s",
                    self.session_id,
                    self.ctx.usage.as_dict(),
                )
                self.service.record_live_usage(self.session_id, self.ctx.usage.as_dict())

    def _load_context(self) -> LiveSessionContext:
        row = self.service.db.get_session(self.session_id)
        if row is None:
            raise ValueError("セッションが見つかりません")
        if row.get("ended_at"):
            raise ValueError("終了済みのセッションです")
        if row.get("io_mode") != "voice":
            raise ValueError("音声セッションではありません")
        config = SessionConfig(
            scene=row["scene"],
            role=row["role"],
            difficulty=row["difficulty"],
            io_mode="voice",
        )
        persona = row["persona"]
        user_profile = self.service.db.get_user_profile(row["user_id"])
        system_instruction = build_partner_system_prompt(config, persona, user_profile)
        voice_name = resolve_live_voice(self.settings, str(persona.get("role", "")))
        return LiveSessionContext(
            session_id=self.session_id,
            config=config,
            persona=persona,
            user_profile=user_profile,
            system_instruction=system_instruction,
            voice_name=voice_name,
        )

    async def _session_loop(self, client: genai.Client) -> None:
        assert self.ctx is not None
        reconnects = 0
        while not self.ctx.closed:
            config = build_live_connect_config(
                self.settings,
                system_instruction=self.ctx.system_instruction,
                voice_name=self.ctx.voice_name,
                resumption_handle=self.ctx.resumption_handle,
            )
            model = self.settings.model_gemini_live
            assert model is not None
            async with client.aio.live.connect(model=model, config=config) as session:
                self._gemini_session = session
                await self._send({"type": "status", "status": "ready"})
                if reconnects == 0 and not self.ctx.resumption_handle:
                    await self._seed_opening(session)
                browser_task = asyncio.create_task(self._pump_browser(session))
                gemini_task = asyncio.create_task(self._pump_gemini(session))
                done, pending = await asyncio.wait(
                    {browser_task, gemini_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                for task in done:
                    exc = task.exception()
                    if exc and not isinstance(exc, WebSocketDisconnect):
                        raise exc
                    if isinstance(exc, WebSocketDisconnect):
                        self.ctx.closed = True
                        return
                if self.ctx.closed:
                    return
                if self.ctx.resumption_handle:
                    reconnects += 1
                    logger.info(
                        "session_id=%s reconnecting via sessionResumption (%s)",
                        self.session_id,
                        reconnects,
                    )
                    await self._send(
                        {
                            "type": "status",
                            "status": "reconnecting",
                            "reconnects": reconnects,
                        }
                    )
                    continue
                return

    async def _seed_opening(self, session: Any) -> None:
        """Seed text opening already stored in DB so Live shares context."""
        turns = self.service.db.list_turns(self.session_id)
        if not turns:
            return
        contents: list[types.Content] = []
        for turn in turns:
            role = "model" if turn["speaker"] == "partner" else "user"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part(text=turn["text"])],
                )
            )
        try:
            await session.send_client_content(turns=contents, turn_complete=False)
        except Exception:
            logger.exception("failed to seed opening history session_id=%s", self.session_id)

    async def _pump_browser(self, session: Any) -> None:
        assert self.ctx is not None
        while not self.ctx.closed:
            raw = await self.websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await self._send({"type": "error", "message": "不正なメッセージです"})
                continue
            mtype = msg.get("type")
            if mtype == "audio":
                data_b64 = msg.get("data") or ""
                try:
                    pcm = base64.b64decode(data_b64)
                except Exception:
                    continue
                if pcm:
                    await session.send_realtime_input(
                        audio=types.Blob(data=pcm, mime_type=INPUT_MIME)
                    )
            elif mtype == "audio_end":
                await session.send_realtime_input(audio_stream_end=True)
            elif mtype == "text":
                text = (msg.get("text") or "").strip()
                if text:
                    await session.send_realtime_input(text=text)
            elif mtype == "end":
                await self._flush_buffers(force=True)
                report = self.service.end_session(self.session_id)
                self.ctx.closed = True
                await self._send({"type": "ended", "report": report})
                return
            elif mtype == "ping":
                await self._send({"type": "pong"})

    async def _pump_gemini(self, session: Any) -> None:
        assert self.ctx is not None
        reconnect_requested = False
        while not self.ctx.closed and not reconnect_requested:
            async for message in session.receive():
                reconnect_requested = await self._handle_server_message(message)
                if self.ctx.closed or reconnect_requested:
                    break
            if reconnect_requested:
                return
            # receive() ends one model turn continuum; continue for next user turn

    async def _handle_server_message(self, message: types.LiveServerMessage) -> bool:
        """Handle one Live server message. Returns True when reconnect is needed."""
        assert self.ctx is not None

        if message.go_away is not None:
            logger.info(
                "session_id=%s go_away time_left=%s",
                self.session_id,
                message.go_away.time_left,
            )
            await self._send(
                {
                    "type": "status",
                    "status": "go_away",
                    "time_left": str(message.go_away.time_left),
                }
            )
            return bool(self.ctx.resumption_handle)

        if message.session_resumption_update is not None:
            upd = message.session_resumption_update
            if upd.new_handle:
                self.ctx.resumption_handle = upd.new_handle
            await self._send(
                {
                    "type": "status",
                    "status": "resumption_update",
                    "resumable": bool(upd.resumable),
                    "has_handle": bool(self.ctx.resumption_handle),
                }
            )

        if message.usage_metadata is not None:
            usage = self.ctx.usage.add(message.usage_metadata)
            logger.info("session_id=%s live_usage %s", self.session_id, usage)
            await self._send({"type": "usage", **usage})

        sc = message.server_content
        if sc is None:
            return False

        if sc.interrupted:
            self.ctx.buffers.partner_interrupted = True
            await self._flush_partner(interrupted=True)
            await self._send({"type": "interrupted"})

        if sc.input_transcription and sc.input_transcription.text:
            chunk = sc.input_transcription.text
            self.ctx.buffers.user_text += chunk
            await self._send(
                {"type": "transcript", "speaker": "user", "text": chunk, "partial": True}
            )

        if sc.output_transcription and sc.output_transcription.text:
            chunk = sc.output_transcription.text
            if not self.ctx.buffers.partner_interrupted:
                self.ctx.buffers.partner_text += chunk
                await self._send(
                    {
                        "type": "transcript",
                        "speaker": "partner",
                        "text": chunk,
                        "partial": True,
                    }
                )

        if sc.model_turn and sc.model_turn.parts:
            for part in sc.model_turn.parts:
                inline = getattr(part, "inline_data", None)
                if inline is not None and inline.data:
                    await self._send(
                        {
                            "type": "audio",
                            "data": base64.b64encode(inline.data).decode("ascii"),
                            "mime": inline.mime_type or f"audio/pcm;rate={OUTPUT_RATE_HZ}",
                            "rate": OUTPUT_RATE_HZ,
                        }
                    )

        if sc.turn_complete:
            await self._flush_user()
            if not self.ctx.buffers.partner_interrupted:
                await self._flush_partner(interrupted=False)
            else:
                self.ctx.buffers.clear_partner()

        return False
    async def _flush_user(self) -> None:
        assert self.ctx is not None
        text = self.ctx.buffers.user_text.strip()
        self.ctx.buffers.user_text = ""
        if not text:
            return
        warnings = format_warnings(check_text(text, self.ctx.config))
        self.service.add_voice_turn(
            self.session_id,
            "user",
            text,
            interrupted=False,
            warnings=warnings or None,
        )
        await self._send(
            {
                "type": "transcript",
                "speaker": "user",
                "text": text,
                "partial": False,
                "warnings": warnings,
            }
        )

    async def _flush_partner(self, *, interrupted: bool) -> None:
        assert self.ctx is not None
        text = self.ctx.buffers.partner_text.strip()
        self.ctx.buffers.partner_text = ""
        flag = interrupted or self.ctx.buffers.partner_interrupted
        self.ctx.buffers.partner_interrupted = False
        if not text:
            return
        self.service.add_voice_turn(
            self.session_id,
            "partner",
            text,
            interrupted=flag,
        )
        await self._send(
            {
                "type": "transcript",
                "speaker": "partner",
                "text": text,
                "partial": False,
                "interrupted": flag,
            }
        )

    async def _flush_buffers(self, *, force: bool = False) -> None:
        if self.ctx is None:
            return
        if force or self.ctx.buffers.user_text.strip():
            await self._flush_user()
        if force or self.ctx.buffers.partner_text.strip():
            await self._flush_partner(interrupted=self.ctx.buffers.partner_interrupted)

    async def _send(self, payload: dict[str, Any]) -> None:
        await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))


async def live_websocket_endpoint(
    websocket: WebSocket,
    session_id: int,
    *,
    settings: Settings | None = None,
    service: SessionService | None = None,
) -> None:
    bridge = LiveBridge(websocket, session_id, settings=settings, service=service)
    await bridge.run()
