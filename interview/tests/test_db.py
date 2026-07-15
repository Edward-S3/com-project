"""Tests for SQLite database layer."""

from pathlib import Path

from app.db import Database


def test_init_schema_and_crud(tmp_path: Path):
    db = Database(tmp_path / "sessions.db")
    db.init_schema()

    user_id = db.create_user(
        name="山田太郎",
        department="製造",
        age=35,
        tenure_years=10,
        grade="4-1",
    )
    user = db.get_user(user_id)
    assert user is not None
    assert user.name == "山田太郎"
    assert user.grade == "4-1"

    session_id = db.create_session(
        user_id=user_id,
        scene="initial",
        role="supervisor",
        difficulty="sme",
        io_mode="text",
        persona={"trait": "modest"},
        models={"partner": "mock"},
    )
    turn_id = db.add_turn(
        session_id=session_id,
        seq=1,
        speaker="user",
        text="目標を確認したいです",
        warnings=["禁止表現（行動目標）: 「向上」"],
    )
    turns = db.list_turns(session_id)
    assert len(turns) == 1
    assert turns[0]["id"] == turn_id
    assert turns[0]["text"] == "目標を確認したいです"
