#!/usr/bin/env python3
"""P2 acceptance scenarios S1-S4 with real API.

Human-side utterances are entered by this script on behalf of the tester.
Exports evidence to docs/acceptance/P2/S1.md .. S4.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_settings
from app.db import Database
from app.models import DIFFICULTY_LABELS, MODE_LABELS, SessionConfig
from app.session_service import SessionService

AGENT_NOTE = "※以下の「利用者」発話は検収用に本スクリプトが代行入力したものです。"
EXPORT_DIR = PROJECT_ROOT / "docs" / "acceptance" / "P2"

S1_CONFIG = SessionConfig("initial", "supervisor", "sme", "text")
S2_CONFIG = SessionConfig("initial", "subordinate", "enterprise", "text")
S3_CONFIG = SessionConfig("final", "supervisor", "sme", "text")
S4_CONFIG = SessionConfig("final", "subordinate", "consultant", "text")

S1_MSGS = [
    "来期の目標を具体的に教えてください。",
    "品質の数値目標が曖昧です。検査ミスや不良の件数で教えてください。",
    "もう少しチャレンジングに。最適水準に近い具体目標に修正しましょう。",
    "よし、それで合意です。面談を終了します。",
]

S2_MSGS_INITIAL = [
    "来期の目標は品質向上を図る。不良を最大10件以下にする。",
    "修正します。QC検定3級社内認定を上期までに取得。改善提案10件(3=2件以下,5=10件,7=15件)",
    "以上です。お願いします。",
]

S2_MSGS_RERUN = [
    "来期の目標は品質向上を図る。不良を最大10件以下にする。",
    "修正します。QC検定3級社内認定を上期までに取得。改善提案10件(3=2件以下,5=10件,7=15件)",
    "以上です。お願いします。",
    "改善提案10件は今期中に達成します。以上で承認お願いします。",
    "10件は昨年実績5件の倍で適切なチャレンジです。承認をお願いします。",
]

S3_MSGS = [
    "自己評価と実績を聞かせてください。",
    "お疲れさま。まず今期の努力はねぎらいます。A評価の根拠を具体的な数値で教えてください。",
    "その根拠だと実績はC相当ですね。期待水準とのギャップを確認しましょう。",
    "面談を終了します。",
]

S4_MSGS = [
    "自己評価はBです。改善提案を8件実施し、歩留を92%から94%に改善しました。",
    "真の目的として、歩留改善が会社の原価戦略にどう貢献したか説明します。",
    "次期は水平展開し費用対効果を測定します。",
]


def _normalize_report(report: dict[str, Any]) -> dict[str, Any]:
    """Rename legacy feedback_flow field for exported evidence."""
    normalized = json.loads(json.dumps(report, ensure_ascii=False))
    flow = normalized.get("feedback_flow_observed")
    if isinstance(flow, dict) and "negligence" in flow and "acknowledgment" not in flow:
        flow["acknowledgment"] = flow.pop("negligence")
    return normalized


def _print_transcript(turns: list[dict]) -> None:
    for t in turns:
        speaker = "利用者" if t["speaker"] == "user" else "AI"
        print(f"[{t['seq']}] {speaker}: {t['text']}")
        if t.get("warnings_json"):
            warnings = json.loads(t["warnings_json"])
            for w in warnings:
                print(f"     ⚠ {w}")


def _format_transcript_md(turns: list[dict]) -> str:
    lines: list[str] = []
    for t in turns:
        speaker = "利用者" if t["speaker"] == "user" else "AI"
        lines.append(f"[{t['seq']}] {speaker}: {t['text']}")
        if t.get("warnings_json"):
            warnings = json.loads(t["warnings_json"])
            for w in warnings:
                lines.append(f"     ⚠ {w}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _user_profile(db: Database, user_id: int) -> dict[str, Any]:
    user = db.get_user(user_id)
    if user is None:
        return {}
    return {
        "name": user.name,
        "department": user.department,
        "age": user.age,
        "tenure_years": user.tenure_years,
        "grade": user.grade,
    }


def _settings_md(
    session: dict[str, Any],
    user_profile: dict[str, Any],
    *,
    extra: str = "",
) -> str:
    persona = session.get("persona") or {}
    mode_code = persona.get("mode_code") or "?"
    lines = [
        f"- モード: {mode_code} ({MODE_LABELS.get(mode_code, '')})",
        f"- 難易度: {DIFFICULTY_LABELS.get(session['difficulty'], session['difficulty'])}",
        f"- 入出力: {'テキスト' if session['io_mode'] == 'text' else '音声'}",
        f"- session_id: {session['id']}",
        f"- 利用者プロフィール: {json.dumps(user_profile, ensure_ascii=False)}",
        f"- ペルソナ要約: {json.dumps(persona, ensure_ascii=False, indent=2)}",
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def _report_from_db(stored: dict[str, Any]) -> dict[str, Any]:
    scores = dict(stored["scores"])
    meta = scores.pop("_meta", {}) or {}
    report = {
        "scores": scores,
        "good_points": stored["good_points"],
        "improvements": stored["improvements"],
        "overall_evaluation": stored["overall_evaluation"],
        "overall_grade": stored["overall_grade"],
        "summary": stored["summary"],
        "model_answer": stored["model_answer"],
        "avg_score": stored["avg_score"],
    }
    report.update(meta)
    return report


def build_session_md(
    db: Database,
    session_id: int,
    report: dict[str, Any] | None,
    *,
    heading: str,
    note: str = "",
) -> str:
    session = db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")
    turns = db.list_turns(session_id)
    if report is None:
        stored = db.get_report(session_id)
        if stored is None:
            raise ValueError(f"Report for session {session_id} not found")
        report = _report_from_db(stored)
    report = _normalize_report(report)
    user_profile = _user_profile(db, session["user_id"])
    parts = [f"# {heading}", ""]
    if note:
        parts.extend([note, ""])
    parts.extend(
        [
            "## (a) 設定",
            "",
            _settings_md(session, user_profile),
            "## (b) トランスクリプト",
            "",
            _format_transcript_md(turns),
            "## (c) 審判出力JSON",
            "",
            "```json",
            json.dumps(report, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(parts)


def _run_dialogue(
    service: SessionService,
    user_id: int,
    config: SessionConfig,
    messages: list[str],
    *,
    persona_trait: str | None = None,
    gap_pattern: str | None = None,
) -> tuple[int, dict[str, Any]]:
    state, opening = service.start_session(
        user_id, config, persona_trait=persona_trait, gap_pattern=gap_pattern
    )
    session_id = state.session_id
    print(f"--- 開始 session_id={session_id} mode={config.mode_code} ---")
    print(f"[opening] AI: {opening}")
    for msg in messages:
        print(f"[利用者・代行] {msg}")
        result = service.send_user_message(session_id, msg)
        if result.get("warnings"):
            print("  [警告]", result["warnings"])
        if result.get("partner_message"):
            print(f"[AI] {result['partner_message']}")
        if result.get("ended"):
            return session_id, result["report"]
    report = service.end_session(session_id)
    return session_id, report


def _export_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {path}")


def run_all_scenarios(service: SessionService, db: Database) -> dict[str, Any]:
    user_id = service.create_user(
        {
            "name": "検収テスト",
            "department": "製造",
            "age": 38,
            "tenure_years": 12,
            "grade": "4-1",
        }
    )
    results: dict[str, Any] = {}

    print("\n======== S1: 1A / 中小企業 ========")
    sid1, rep1 = _run_dialogue(service, user_id, S1_CONFIG, S1_MSGS, persona_trait="modest")
    results["S1"] = {"session_id": sid1, "report": rep1}
    _print_transcript(db.list_turns(sid1))

    print("\n======== S2: 1B / 大企業 (初回) ========")
    user2 = service.create_user(
        {"name": "検収1B", "department": "品質保証", "age": 30, "tenure_years": 6, "grade": "3"}
    )
    sid2, rep2 = _run_dialogue(service, user2, S2_CONFIG, S2_MSGS_INITIAL)
    results["S2_initial"] = {"session_id": sid2, "report": rep2}
    _print_transcript(db.list_turns(sid2))

    print("\n======== S3: 2A / 中小企業 / gap-b ========")
    user3 = service.create_user(
        {"name": "検収2A", "department": "製造", "age": 42, "tenure_years": 18, "grade": "4-2"}
    )
    sid3, rep3 = _run_dialogue(service, user3, S3_CONFIG, S3_MSGS, gap_pattern="b")
    results["S3"] = {"session_id": sid3, "report": rep3}
    _print_transcript(db.list_turns(sid3))

    print("\n======== S4: 2B / コンサルタント ========")
    user4 = service.create_user(
        {"name": "検収2B", "department": "生産技術", "age": 34, "tenure_years": 9, "grade": "4-1"}
    )
    sid4, rep4 = _run_dialogue(service, user4, S4_CONFIG, S4_MSGS)
    results["S4"] = {"session_id": sid4, "report": rep4}
    _print_transcript(db.list_turns(sid4))

    return results


def run_s2_rerun(service: SessionService, db: Database) -> tuple[int, dict[str, Any]]:
    print("\n======== S2: 1B / 大企業 (再実行・期限補完) ========")
    user2 = service.create_user(
        {"name": "検収1B再実行", "department": "品質保証", "age": 30, "tenure_years": 6, "grade": "3"}
    )
    sid, rep = _run_dialogue(service, user2, S2_CONFIG, S2_MSGS_RERUN)
    _print_transcript(db.list_turns(sid))
    return sid, rep


def export_p2_docs(
    db: Database,
    *,
    s1_session: int,
    s2_initial_session: int,
    s2_rerun_session: int,
    s2_rerun_report: dict[str, Any],
    s3_session: int,
    s4_session: int,
) -> None:
    s1_md = build_session_md(
        db,
        s1_session,
        report=None,
        heading="S1: 1A / 中小企業",
        note=AGENT_NOTE,
    )
    s2_initial_md = build_session_md(
        db,
        s2_initial_session,
        report=None,
        heading="S2: 1B / 大企業（初回・参考）",
        note=(
            "初回実行では最終ターンでAI上司が改善提案の期限追及のみで完全承認に至らなかった。"
            "き(期限)に忠実な正しい挙動と判断する。"
        ),
    )
    s2_rerun_md = build_session_md(
        db,
        s2_rerun_session,
        report=_normalize_report(s2_rerun_report),
        heading="S2: 1B / 大企業（P2.1-1修正後・正）",
        note=(
            "P2.1-1承認ゲート強化後の再実行。"
            "期待順序: 期限なし申告→差し戻し→期限補完→承認。"
            "ターン3「以上です」では承認せず期限追及、ターン4で承認。"
        ),
    )
    s2_md = (
        "# S2: 1B / 大企業\n\n"
        f"{AGENT_NOTE}\n\n"
        "本シナリオは初回実行（参考）と再実行（正）の2通りを併記する。\n\n"
        "---\n\n"
        + s2_initial_md.replace("# S2: 1B / 大企業（初回・参考）\n\n", "")
        + "\n---\n\n"
        + s2_rerun_md.replace("# S2: 1B / 大企業（再実行・正）\n\n", "")
    )
    s3_md = build_session_md(
        db,
        s3_session,
        report=None,
        heading="S3: 2A / 中小企業 / gap-b",
        note=AGENT_NOTE,
    )
    s4_md = build_session_md(
        db,
        s4_session,
        report=None,
        heading="S4: 2B / コンサルタント",
        note=AGENT_NOTE,
    )

    _export_file(EXPORT_DIR / "S1.md", s1_md)
    _export_file(EXPORT_DIR / "S2.md", s2_md)
    _export_file(EXPORT_DIR / "S3.md", s3_md)
    _export_file(EXPORT_DIR / "S4.md", s4_md)


def main() -> int:
    parser = argparse.ArgumentParser(description="P2 acceptance runner and exporter")
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Run all S1-S4 scenarios (full API run)",
    )
    parser.add_argument(
        "--rerun-s2",
        action="store_true",
        help="Re-run S2 only with deadline completion utterance",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export docs/acceptance/P2/S1.md .. S4.md from DB sessions",
    )
    parser.add_argument("--s1-session", type=int, default=1)
    parser.add_argument("--s2-initial-session", type=int, default=2)
    parser.add_argument("--s2-rerun-session", type=int, required=False)
    parser.add_argument("--s3-session", type=int, default=3)
    parser.add_argument("--s4-session", type=int, default=4)
    args = parser.parse_args()

    if not (args.run_all or args.rerun_s2 or args.export):
        parser.error("Specify at least one of --run-all, --rerun-s2, --export")

    print(AGENT_NOTE)
    settings = load_settings()
    if (args.run_all or args.rerun_s2) and not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY required for acceptance")
        return 1

    service = SessionService(settings)
    db = Database(settings.db_path)
    db.init_schema()

    s2_rerun_session = args.s2_rerun_session
    s2_rerun_report: dict[str, Any] | None = None

    if args.run_all:
        results = run_all_scenarios(service, db)
        args.s1_session = results["S1"]["session_id"]
        args.s2_initial_session = results["S2_initial"]["session_id"]
        args.s3_session = results["S3"]["session_id"]
        args.s4_session = results["S4"]["session_id"]
        print("\n======== 審判サマリ ========")
        for name, data in results.items():
            print(f"\n--- {name} session={data['session_id']} ---")
            print(json.dumps(_normalize_report(data["report"]), ensure_ascii=False, indent=2))

    if args.rerun_s2:
        s2_rerun_session, s2_rerun_report = run_s2_rerun(service, db)
        print("\n--- S2 rerun judge ---")
        print(json.dumps(_normalize_report(s2_rerun_report), ensure_ascii=False, indent=2))

    if args.export:
        if s2_rerun_session is None:
            print("ERROR: --export requires --s2-rerun-session or --rerun-s2")
            return 1
        export_p2_docs(
            db,
            s1_session=args.s1_session,
            s2_initial_session=args.s2_initial_session,
            s2_rerun_session=s2_rerun_session,
            s2_rerun_report=(
                s2_rerun_report
                if s2_rerun_report is not None
                else _report_from_db(db.get_report(s2_rerun_session) or {})
            ),
            s3_session=args.s3_session,
            s4_session=args.s4_session,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
