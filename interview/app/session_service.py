"""Session orchestration: turns, end conditions, judging."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.checker import check_text, format_warnings
from app.config import Settings, load_settings
from app.db import Database
from app.goal_readiness import assess_goal_readiness, contains_approval_declaration
from app.judge import JudgeService
from app.models import MAX_TURNS_DEFAULT, SessionConfig
from app.partner import PartnerService
from app.persona import build_persona
from app.providers.registry import get_provider_for_role
from app.rubrics_util import strip_hidden_facts

_INTERRUPTED_MARKER = "[interrupted]"


def _warnings_indicate_interrupted(warnings_json: str | None) -> bool:
    """True when warnings_json list contains an [interrupted] marker string."""
    if warnings_json is None:
        return False
    if not isinstance(warnings_json, str):
        return False
    text = warnings_json.strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(parsed, list):
        return False
    return any(
        isinstance(item, str) and _INTERRUPTED_MARKER in item for item in parsed
    )


def _build_judge_transcript(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build judge transcript; mark interrupted turns only when warnings say so."""
    transcript: list[dict[str, Any]] = []
    for turn in turns:
        entry: dict[str, Any] = {
            "speaker": turn["speaker"],
            "text": turn["text"],
        }
        if _warnings_indicate_interrupted(turn.get("warnings_json")):
            entry["interrupted"] = True
        transcript.append(entry)
    return transcript


@dataclass
class SessionState:
    session_id: int
    user_id: int
    config: SessionConfig
    user_profile: dict[str, Any]
    persona: dict[str, Any] = field(repr=False)
    turn_count: int = 0
    ended: bool = False
    end_reason: str | None = None


class SessionService:
    def __init__(self, settings: Settings | None = None, db: Database | None = None) -> None:
        self.settings = settings or load_settings()
        self.db = db or Database(self.settings.db_path)
        self.db.init_schema()
        self._migrate_reports()
        self.partner = PartnerService(self.settings)
        self.judge = JudgeService(self.settings)

    def _migrate_reports(self) -> None:
        with self.db.connect() as conn:
            try:
                conn.execute("ALTER TABLE reports ADD COLUMN overall_evaluation TEXT")
            except Exception:
                pass

    def create_user(self, profile: dict[str, Any]) -> int:
        return self.db.create_user(
            name=profile["name"],
            department=profile["department"],
            age=int(profile["age"]),
            tenure_years=int(profile["tenure_years"]),
            grade=profile["grade"],
        )

    def list_users(self) -> list[dict[str, Any]]:
        return self.db.list_users()

    def start_session(
        self,
        user_id: int,
        config: SessionConfig,
        *,
        persona_trait: str | None = None,
        gap_pattern: str | None = None,
    ) -> tuple[SessionState, str]:
        user = self.db.get_user(user_id)
        if user is None:
            raise ValueError("User not found")
        user_profile = {
            "id": user.id,
            "name": user.name,
            "department": user.department,
            "age": user.age,
            "tenure_years": user.tenure_years,
            "grade": user.grade,
        }
        persona = build_persona(
            self.settings,
            user_profile,
            config,
            trait=persona_trait,
            gap_pattern=gap_pattern,
            use_llm=False,
        )
        models = {
            "partner": get_provider_for_role(self.settings, "partner", allow_mock=True).name,
            "judge": get_provider_for_role(self.settings, "judge", allow_mock=True).name,
            "persona": get_provider_for_role(self.settings, "persona", allow_mock=True).name,
        }
        session_id = self.db.create_session(
            user_id=user_id,
            scene=config.scene,
            role=config.role,
            difficulty=config.difficulty,
            io_mode=config.io_mode,
            persona=persona,
            models=models,
        )
        opening = self.partner.generate_opening(config, persona, user_profile)
        self.db.add_turn(session_id, 1, "partner", opening)
        state = SessionState(
            session_id=session_id,
            user_id=user_id,
            config=config,
            user_profile=user_profile,
            persona=persona,
            turn_count=1,
        )
        return state, opening

    def send_user_message(
        self,
        session_id: int,
        text: str,
        *,
        end_requested: bool = False,
    ) -> dict[str, Any]:
        session_row = self.db.get_session(session_id)
        if session_row is None:
            raise ValueError("Session not found")
        if session_row.get("ended_at"):
            raise ValueError("Session already ended")

        config = SessionConfig(
            scene=session_row["scene"],
            role=session_row["role"],
            difficulty=session_row["difficulty"],
            io_mode=session_row["io_mode"],
        )
        persona = session_row["persona"]
        user_profile = self.db.get_user_profile(session_row["user_id"])
        turns = self.db.list_turns(session_id)
        seq = len(turns) + 1

        warnings = format_warnings(check_text(text, config))
        self.db.add_turn(session_id, seq, "user", text, warnings=warnings or None)

        if end_requested or "面談を終了" in text:
            self.db.end_session(session_id, "user_declared")
            report = self._judge_and_save(session_id, config, persona, user_profile)
            return {
                "ended": True,
                "end_reason": "user_declared",
                "warnings": warnings,
                "report": report,
            }

        history = [{"speaker": t["speaker"], "text": t["text"]} for t in turns]
        history.append({"speaker": "user", "text": text})
        user_goal_texts = [
            t["text"] for t in turns if t["speaker"] == "user"
        ] + [text]
        reply = self.partner.reply(config, persona, user_profile, history, text)
        reply = self._guard_1b_approval(
            config, persona, user_profile, history, text, user_goal_texts, reply
        )
        self.db.add_turn(session_id, seq + 1, "partner", reply)

        partner_end = False
        if config.role in ("subordinate",) and config.ai_role == "supervisor":
            partner_end = self.partner.should_end_as_supervisor(reply)
        if partner_end:
            self.db.end_session(session_id, "ai_declared")
            report = self._judge_and_save(session_id, config, persona, user_profile)
            return {
                "ended": True,
                "end_reason": "ai_declared",
                "partner_message": reply,
                "warnings": warnings,
                "report": report,
            }

        max_turns = MAX_TURNS_DEFAULT
        if seq + 1 >= max_turns:
            self.db.end_session(session_id, "turn_limit")
            report = self._judge_and_save(session_id, config, persona, user_profile)
            return {
                "ended": True,
                "end_reason": "turn_limit",
                "partner_message": reply,
                "warnings": warnings,
                "report": report,
            }

        return {
            "ended": False,
            "partner_message": reply,
            "warnings": warnings,
        }

    def add_voice_turn(
        self,
        session_id: int,
        speaker: str,
        text: str,
        *,
        interrupted: bool = False,
        warnings: list[str] | None = None,
    ) -> int:
        """Persist a voice-mode transcript turn (audio itself is never stored)."""
        turns = self.db.list_turns(session_id)
        seq = len(turns) + 1
        warn_list = list(warnings or [])
        if interrupted:
            warn_list.append("[interrupted] 打ち切り位置までの再生分のみ（以降は評価対象外）")
        return self.db.add_turn(
            session_id,
            seq,
            speaker,
            text,
            audio_flag=True,
            warnings=warn_list or None,
        )

    def record_live_usage(self, session_id: int, usage: dict[str, Any]) -> None:
        """Merge Live API token usage into sessions.models_json for cost tracking."""
        row = self.db.get_session(session_id)
        if row is None:
            return
        models = dict(row.get("models") or {})
        models["live_usage"] = usage
        if self.settings.model_gemini_live:
            models["live_model"] = self.settings.model_gemini_live
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE sessions SET models_json = ? WHERE id = ?",
                (json.dumps(models, ensure_ascii=False), session_id),
            )

    def end_session(self, session_id: int) -> dict[str, Any]:
        session_row = self.db.get_session(session_id)
        if session_row is None:
            raise ValueError("Session not found")
        config = SessionConfig(
            scene=session_row["scene"],
            role=session_row["role"],
            difficulty=session_row["difficulty"],
            io_mode=session_row["io_mode"],
        )
        persona = session_row["persona"]
        user_profile = self.db.get_user_profile(session_row["user_id"])
        self.db.end_session(session_id, "user_button")
        return self._judge_and_save(session_id, config, persona, user_profile)

    def _guard_1b_approval(
        self,
        config: SessionConfig,
        persona: dict[str, Any],
        user_profile: dict[str, Any],
        history: list[dict[str, str]],
        user_message: str,
        user_goal_texts: list[str],
        reply: str,
    ) -> str:
        if config.mode_code != "1B":
            return reply
        if not contains_approval_declaration(reply):
            return reply
        readiness = assess_goal_readiness(user_goal_texts)
        if readiness.ready_for_approval:
            return reply
        missing = "、".join(readiness.missing)
        hint = (
            f"直前の応答に承認が含まれていたが、部下申告はまだ不足がある（{missing}）。"
            "承認宣言は禁止。不足観点を具体的に追及し差し戻すこと。"
            "「承認します」「承認済み」は使用しない。"
        )
        return self.partner.reply(
            config,
            persona,
            user_profile,
            history,
            user_message,
            correction_hint=hint,
        )

    def _judge_and_save(
        self,
        session_id: int,
        config: SessionConfig,
        persona: dict[str, Any],
        user_profile: dict[str, Any],
    ) -> dict[str, Any]:
        turns = self.db.list_turns(session_id)
        transcript = _build_judge_transcript(turns)
        result = self.judge.evaluate(config, persona, transcript, user_profile)
        scores = result.get("scores", {})
        avg = result.get("avg_score")
        if avg is None and scores:
            avg = sum(scores.values()) / len(scores)
        self.db.create_report(
            session_id=session_id,
            scores=scores,
            good_points=result.get("good_points", []),
            improvements=result.get("improvements", []),
            overall_grade=result.get("overall_grade"),
            overall_evaluation=result.get("overall_evaluation", ""),
            summary=result.get("summary", ""),
            model_answer=result.get("model_answer"),
            avg_score=float(avg) if avg is not None else None,
            extra={
                "goal_level_percent": result.get("goal_level_percent"),
                "goal_level_reached": result.get("goal_level_reached"),
                "feedback_flow_observed": result.get("feedback_flow_observed"),
            },
        )
        return result

    def get_session_public(self, session_id: int) -> dict[str, Any]:
        row = self.db.get_session(session_id)
        if row is None:
            raise ValueError("Session not found")
        persona = strip_hidden_facts(row["persona"])
        turns = self.db.list_turns(session_id)
        report = self.db.get_report(session_id)
        return {
            "session": {**row, "persona": persona},
            "turns": turns,
            "report": report,
        }
