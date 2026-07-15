"""Tests for session_service judge transcript helpers (D-08)."""

from __future__ import annotations

import json

from app.session_service import _build_judge_transcript, _warnings_indicate_interrupted


def test_build_judge_transcript_marks_interrupted_from_warnings_json():
    turns = [
        {
            "speaker": "partner",
            "text": "途中まで話した内容",
            "warnings_json": json.dumps(
                ["[interrupted] 打ち切り位置までの再生分のみ（以降は評価対象外）"],
                ensure_ascii=False,
            ),
        },
        {
            "speaker": "user",
            "text": "通常の発話",
            "warnings_json": None,
        },
        {
            "speaker": "user",
            "text": "警告のみ",
            "warnings_json": json.dumps(
                ['参考警告（行動目標）: 「向上」'],
                ensure_ascii=False,
            ),
        },
    ]
    transcript = _build_judge_transcript(turns)
    assert transcript[0] == {
        "speaker": "partner",
        "text": "途中まで話した内容",
        "interrupted": True,
    }
    assert transcript[1] == {"speaker": "user", "text": "通常の発話"}
    assert "interrupted" not in transcript[1]
    assert transcript[2] == {"speaker": "user", "text": "警告のみ"}
    assert "interrupted" not in transcript[2]


def test_build_judge_transcript_tolerates_null_empty_invalid_warnings():
    turns = [
        {"speaker": "user", "text": "a", "warnings_json": None},
        {"speaker": "user", "text": "b", "warnings_json": ""},
        {"speaker": "user", "text": "c", "warnings_json": "   "},
        {"speaker": "user", "text": "d", "warnings_json": "{not json"},
        {"speaker": "user", "text": "e", "warnings_json": "123"},
    ]
    transcript = _build_judge_transcript(turns)
    assert all("interrupted" not in row for row in transcript)
    assert len(transcript) == 5
    assert _warnings_indicate_interrupted(None) is False
    assert _warnings_indicate_interrupted("") is False
    assert _warnings_indicate_interrupted("{not json") is False


def test_build_judge_transcript_interrupted_with_other_warnings():
    warnings = json.dumps(
        [
            '参考警告（行動目標）: 「向上」',
            "[interrupted] 打ち切り位置までの再生分のみ（以降は評価対象外）",
        ],
        ensure_ascii=False,
    )
    turns = [{"speaker": "partner", "text": "混在", "warnings_json": warnings}]
    transcript = _build_judge_transcript(turns)
    assert transcript[0]["interrupted"] is True
    assert _warnings_indicate_interrupted(warnings) is True
