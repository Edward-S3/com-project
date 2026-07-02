# NotebookLM スライド画像 → 編集可能PPTX 変換ツール 開発指示書

---

## ロール設定

あなたは優秀なPythonエンジニアです。
画像処理・OCR・プレゼンテーション自動化に精通しており、保守性の高いモジュール設計ができます。

---

## タスク概要

NotebookLMのStudio機能が出力した `.pptx` ファイル（各スライドに1枚の画像が埋め込まれた非編集状態）を入力とし、
Gemini APIによるOCR・レイアウト解析と `python-pptx` を用いて、テキスト・図形・背景を要素ごとに分離・再配置した
**編集可能な新しい `.pptx` ファイル**を生成するPythonツールを開発してください。

### 品質前提（コード内コメントにも明記すること）

> このツールはOCRと画像解析に基づく**近似復元**であり、元スライドのフォント・配色・レイアウトを
> 完全に再現するものではありません。出力PPTXは「テキスト編集の起点」として使用することを想定しています。

---

## 入出力仕様

| 項目 | 内容 |
|------|------|
| 入力 | 各スライドに1枚の画像が埋め込まれた `.pptx` ファイル（NotebookLM出力） |
| 出力 | テキストボックス・画像オブジェクトとして要素を再配置した編集可能な新しい `.pptx` ファイル |

---

## モジュール構成（5ファイル構成で実装すること）

```
project/
├── main.py              # エントリーポイント・全体統合
├── pptx_loader.py       # PPTXから埋め込み画像を抽出
├── image_parser.py      # Gemini APIによるOCR・レイアウト解析
├── structure_builder.py # 中間JSON構造の生成・検証
├── pptx_writer.py       # 中間JSONをもとにPPTXを再構築
├── requirements.txt     # 依存ライブラリ一覧
└── output/              # 生成されたPPTXの出力先
```

---

## 中間表現（JSON構造）— 重要

各スライドの解析結果は以下のJSON形式で保持・受け渡しすること。
この中間表現がモジュール間の共通インターフェースとなります。

```json
{
  "slide_index": 0,
  "slide_width_px": 1280,
  "slide_height_px": 720,
  "image_dpi": 96,
  "text_blocks": [
    {
      "id": "t_0",
      "content": "スライドタイトル",
      "role": "title",
      "bbox": { "x": 80, "y": 40, "width": 900, "height": 80 },
      "font_size_pt": 28.0
    },
    {
      "id": "t_1",
      "content": "箇条書きテキスト",
      "role": "body",
      "bbox": { "x": 80, "y": 140, "width": 800, "height": 30 },
      "font_size_pt": 16.0
    }
  ],
  "image_blocks": [
    {
      "id": "img_0",
      "bbox": { "x": 900, "y": 200, "width": 320, "height": 240 },
      "cropped_path": "temp/slide_0_img_0.png"
    }
  ]
}
```

`role` の値: `"title"` / `"body"` / `"caption"` / `"other"`

---

## 実装ステップ

**一度に全コードを出力せず、以下のStepを1つずつ提示し、私の承認を得てから次のStepに進んでください。**

---

### Step 1: 環境構築

`requirements.txt` と以下のディレクトリ構成を作成してください。

```
google-generativeai
python-pptx
Pillow
opencv-python
```

---

### Step 2: `pptx_loader.py` — 埋め込み画像の抽出

- `python-pptx` で入力 `.pptx` を開く
- 各スライドの `shapes` を走査し `shape.shape_type == MSO_SHAPE_TYPE.PICTURE` に該当するものを取得する
- `shape.image.blob` を `io.BytesIO` 経由で `PIL.Image` オブジェクトに変換する（ディスク書き出し不要）
- 1スライドに複数Pictureがある場合は最大面積のものをメインとみなす
- `shape_type` で判定できない場合のフォールバック: `shape.has_text_frame is False` かつ `shape.name` に `"Picture"` を含むものを候補とする

実装イメージ（参考）:

```python
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from PIL import Image
import io

prs = Presentation("input.pptx")
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            img = Image.open(io.BytesIO(shape.image.blob))
            # → image_parser.py に渡す
```

---

### Step 3: `image_parser.py` — Gemini APIによるOCR・レイアウト解析

Gemini API（`gemini-1.5-pro` 推奨）に抽出した画像を渡し、
テキスト領域と画像領域の座標・内容を **Step 2で定義した中間JSON形式** で返すよう設計してください。

#### Gemini APIへのシステムプロンプト要件

以下の条件を満たすシステムプロンプトをあなたが工夫して記述してください:

- 出力は **JSONのみ**（前置き文・Markdownコードフェンス不可）
- `text_blocks` には `content`・`role`（title/body/caption/other）・`bbox`（x/y/width/height、ピクセル値）を含める
- `image_blocks` には `bbox`（x/y/width/height、ピクセル値）を含める
- 日本語・英語の混在テキストに対応すること
- フォントサイズは bbox の高さから推定し `font_size_pt` として返すこと（推定式: `bbox_height_px ÷ dpi × 72`）

#### エラーハンドリング

- API呼び出し失敗時は該当スライドをログに記録し、元画像をそのまま貼り付けるフォールバックに移行する
- JSONパース失敗時はリトライを1回行い、それでも失敗した場合はフォールバックとする

---

### Step 4: `structure_builder.py` — 中間JSON構造の生成・検証

- Step 3のGemini API出力JSONを受け取り、スキーマ検証を行う
- `image_blocks` の座標をもとに元画像から該当領域を `Pillow` でクロップし、一時ファイルに保存する（`temp/slide_{n}_img_{m}.png`）
- クロップ済みパスを `cropped_path` に格納し、最終的な中間JSONを返す

---

### Step 5: `pptx_writer.py` — 編集可能PPTXの生成

- `python-pptx` で新規プレゼンテーションを作成
- スライドサイズは元PPTXから取得する（取得できない場合は 16:9 デフォルト: 幅 33.87cm × 高さ 19.05cm）
- 中間JSONをもとに以下を配置する:

| 要素 | 配置方法 | 注意点 |
|------|----------|--------|
| 背景 | テキスト・図形領域をマスクした背景画像を最背面に配置 | |
| テキストブロック | `TextBox` として配置 | `role == "title"` は太字・大きめフォント |
| 画像ブロック | `cropped_path` の画像を `Picture` として配置 | |

#### 座標変換（必須）

```
EMU = px × 9525 ÷ (image_dpi ÷ 96)
```

- `image_dpi` は Step 2 で取得した実際の値を使用する
- デフォルト dpi が取得できない場合は 96 を使用する

---

### Step 6: `main.py` — 全体統合

- コマンドライン引数で入力 `.pptx` パスを受け取る
- Step 2〜5 を順次呼び出し、`output/` フォルダに変換済み `.pptx` を出力する
- 各スライドの処理結果（検出テキスト数・画像ブロック数・フォールバック発生有無）をコンソールにログ出力する

実行例:
```bash
python main.py --input input.pptx --output output/result.pptx
```

---

## 共通実装要件

1. **日本語対応**: Gemini APIは多言語対応のため設定不要だが、テキストの文字化け防止のため出力PPTXのデフォルトフォントは `"Yu Gothic"` を指定すること
2. **エラーハンドリング**: 各モジュールは例外をキャッチしてログを出力し、処理継続可能な場合はスキップ・フォールバックする
3. **ログ出力**: `logging` モジュールを使用し、`INFO` / `WARNING` / `ERROR` レベルを適切に使い分ける
4. **一時ファイル**: `temp/` フォルダを使用し、処理完了後に自動削除する
5. **環境変数**: Gemini API キーは `.env` ファイルから `python-dotenv` で読み込む（コードにハードコードしない）

---

## 最初の確認事項

Step 1 に着手する前に、以下を確認してください:

1. Gemini API キーは取得済みですか？（`GEMINI_API_KEY` として `.env` に設定）
2. 入力 `.pptx` ファイルのパスを教えてください
3. Python バージョンは 3.9 以上ですか？
