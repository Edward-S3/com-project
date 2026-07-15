# P3受入ログ 2026-07-15

## 本日の結果サマリ
- D-07: 修正完了・受入OK(コミット 021074e)
- D-08: 修正完了・受入OK(コミット 828724c)
- session 17 実データ再採点により両修正の実効を確認
- **審判パートの差し戻しを解除し、P3審判パートを合格と判定**
- D-06は未着手(P3残件として継続)

## D-07 受入記録
- 指示書: docs/D-07_FIX_INSTRUCTIONS.md(コミット 467559d)
- 実装: 尺度の動的導出(rating_scale由来3-7)+normalize_judge_scores
  (クランプ/round/数値化不能除外/score_corrections記録/avg_score再計算)
- 検証: 変更範囲2ファイル確認、diff実地確認、pytest 6件実地パス
- 再採点実証: score_corrections=null(補正ゼロ、プロンプト修正の実効確認)

## D-08 受入記録
- 指示書: docs/D-08_FIX_INSTRUCTIONS.md(コミット bd66c83)
- 実装: フィールド方式(_build_judge_transcript + 
  format_transcript_for_judge + プロンプト採点ルール追加)
- 検証: 変更範囲4ファイル確認、diff実地確認、pytest全体46件実地パス
- 再採点実証: session 17のseq 6・8に(途中打ち切り)ラベル付与を確認。
  good_pointsの引用に打ち切り発話は不使用

## session 17 再採点記録(/tmp/rejudge_s17.py、DB書き込みなし)
- scores: 全観点3-4(範囲内)、avg_score=3.29、overall_grade=D
  (本セッションはバージイン検証用のためDは想定内、採点自体は正常動作)
- transcript 13件中 interrupted=2件を正しくマーク

## 既知事項・申し送り
- リポジトリ全体がCRLF改行(初回コミット時点から)。実害なしと判断し
  今回スコープ外。改行統一+.gitattributes導入は別タスクとして扱う
- score_correctionsはレスポンスのみでDB非永続化(P4検討事項)
- D-06(テキスト履歴→音声切替で_seed_opening後にGemini 1007)が
  P3の最終残件

## D-06 調査記録(7/15午後)
- ログ解析: 発生実体はsession 14(7/14 15:00:35接続→15:01:27に1007)。
  _pump_gemini受信側で顕在化、接続後52秒間サーバー応答ログなし
- 棄却した仮説: history_config宣言欠落(実装済みと確認)/role構成・
  turn_complete組合せ(最小再現4パターン全てOK)/シード後音声送信
  (3パターンOK)/session 14実データでの完全再現(OK)/実UI再現
  2回(session 18・19、同一シード条件でいずれも正常)
- 結論: 現時点で再現不能。preview版モデルのサーバー側判定変更により
  解消された可能性が最も高い
- 処置: 条件付きクローズ(監視継続)。再発時は再オープンし、
  live_bridgeへの送信ペイロード一時ログ追加を初手とする
- 潜在的懸念(未対処): _seed_openingのturn_complete=False、
  送信内容のログ不在。再発時の調査コスト低減のため改善候補

## 改善提案(P4候補への追記)
- 面談終了後、審判の採点完了まで数十秒無反応となりユーザーが
  スタックと誤認する。「採点中」のローディング表示追加を提案
