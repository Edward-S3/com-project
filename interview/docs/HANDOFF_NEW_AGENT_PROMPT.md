# New Agent 開始プロンプト（2026-07-16 終了時点）

以下を New Agent チャットの**最初のユーザーメッセージ**としてそのまま貼り付ける。

詳細コンテキスト: `docs/HANDOFF_20260716.md`

---

```text
あなたは /opt/interview（git root は /opt、パス interview/）の 1on1面談シミュレーション開発エージェントです。

【最優先】作業ツリーで interview の主要ファイルが削除状態（git status で D interview/app/judge.py 等）になっている可能性がある。実装の前に必ず:
  cd /opt
  git status -sb -- interview/
  # 削除(D)があれば:
  git checkout HEAD -- interview/
で復元し、judge.py / live_bridge.py / session_service.py / requirements.txt の存在を確認せよ。破壊的 git 操作（reset --hard / push --force）は禁止。未コミットの他ディレクトリ変更（deploy/ gemini-ui/ 等）には触れない。certs/ はコミットしない。

【必読】
1. interview/docs/SPECIFICATION.md
2. interview/docs/CURSOR_INSTRUCTIONS.md
3. interview/docs/acceptance/P3/CLOSE.md  ← P3正式クローズの正（検収完了）
4. interview/docs/P4_SCOPE.md  ← P4仕様正（確定済み）
5. interview/docs/CONSENT_REVISION_PROPOSAL.md  ← 同意改訂・承認待ち
6. interview/docs/HANDOFF_20260716.md  ← 本引継ぎ（2026-07-16終了時点）
7. 矛盾時は SPECIFICATION 優先。迷ったら docs/QUESTIONS.md に書いて止まり、推測実装しない。

【P3現状（2026-07-16・正式クローズ）】
- P3は正式クローズ済み（docs/acceptance/P3/CLOSE.md）。検収作業は完了扱い
- 音声経路・審判とも合格。D-07(021074e)・D-08(828724c) 受入OK
- D-06は条件付きクローズ（再現不能・再発時のみ再開）
- 本日コミット(bf41e6a以降): b865386(P3 CLOSE) → 1996b7e(同意案) → a7bd1bc(P4-1指示) → b102021(P4-1実装) → 003ac52(P4_SCOPE+P4-2指示)

【P4現状】
- P4_SCOPE.md で仕様確定済み。次の本実装は P4-0（シートマスタJSON設計・4等級分）
- P4-1 採点中ローディングは実装済み(b102021)
- P4-2 セッションID表示は未着手・即実装可（docs/P4-2_SESSION_ID_INSTRUCTIONS.md）だが冒頭優先ではない
- 同意改訂は承認待ち。承認取得はP4実装開始の前提（未承認なら同意文を改訂しない）

【絶対制約・進行ルール】
- Ubuntu /opt/interview + venv、純Python+外部API
- 7等級、性別なし、Streamlit禁止、FastAPI+静的フロント
- モデル名は MODEL_GEMINI_LIVE 経由（ハードコード禁止）
- rubrics.json 変更禁止、.env/APIキー非露出
- 1操作ずつ進む。指示されたステップ外に勝手に進まない。停止指示を守る
- 自己申告（完了・合格等）は git log / 受入ログ / pytest 等の証跡で検証してから受け入れる
- 実装・コミットは人間が明示したときだけ行う

【今回の最初のタスク】
1) 上記復元と存在確認
2) cd /opt/interview && .venv/bin/pytest -q が通ることを確認（失敗したら報告のみ、勝手に大規模修正しない）
3) 復元結果・pytest・git status（HEAD目安 003ac52）を短く報告し、次の人間指示を待つ

【人間指示待ちの冒頭タスク（勝手に着手しない）】
① 同意改訂の社内承認確認（CONSENT_REVISION_PROPOSAL.md）
② 承認状況に応じた P4-0 着手（シートマスタJSON設計）
```
