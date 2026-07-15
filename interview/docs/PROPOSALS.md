# 提案事項（PROPOSALS.md）

> 仕様外の機能追加や構成変更の提案を記載する。人間の承認前に実装しない。

---

## P-01: P1 作業順序の提案

承認後の推奨実施順:

1. `python -m venv /opt/interview/.venv`（Python 3.10.12）と `requirements.txt` 作成
2. `/opt/interview/.gitignore` 作成（`.env`, `.venv/`, `data/sessions.db`, `logs/`, `__pycache__/`）
3. `.env.example` 作成（キー名のみ、値はプレースホルダ）
4. `data/grades.json`, `data/rubrics.json` 作成
5. `app/config.py` → `app/providers/` → `app/db.py` → `app/checker.py`
6. `app/main.py`（FastAPI 最小雛形: `/health` のみ）
7. `tests/` と CLI スクリプト（例: `scripts/cli_chat.py`）
8. `README.md`, `pyproject.toml`（ruff）, `docs/CHANGELOG.md` 初回追記

**承認**: 承認（venv は Python 3.10.12 で構築）

---

## P-02: P1 では `main.py` を health チェックのみに留める提案

SPEC P1 は「FastAPI 雛形」を含むが、画面 S-01〜S-05 は P2。P1 の FastAPI は `/health` と静的ファイルマウント準備のみとし、対話検証は CLI に集約する。これにより P2 で UI を一括実装しやすい。

**承認**: 承認。P1のFastAPIは /health と静的マウント準備のみとする。
---

## P-03: `scripts/` ディレクトリの追加

仕様 6.2 に `scripts/` はないが、P1 受入の「CLI スクリプト」用に `scripts/cli_chat.py` を置くことを提案する。`app/` 内に CLI を置く代替も可能。構成変更のため承認を推奨。

**承認**: 承認。scripts/cli_chat.py を追加してよい。CURSOR_INSTRUCTIONS.md セクション4の構成にscripts/を追記すること。