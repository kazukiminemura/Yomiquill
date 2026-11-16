# Yomiquill（ヨミクイル）

**手元の資料を読み、回答の根拠を原本までたどれる Windows アプリです。**

PDF・Office 文書・画像などを追加すると、日本語で質問したり要約したりできます。モデルの取得後は OCR・検索・回答生成を PC 上で実行できます。

## はじめる

**Windows x64、64 ビット版 Python 3.12、モデル用の空き容量約 5.8 GB** が必要です。初回は依存ライブラリとモデルの取得にインターネット接続を使います。

[ソース ZIP をダウンロード](https://github.com/kazukiminemura/Yomiquill/archive/refs/heads/main.zip)して展開し、`desktop.py` があるフォルダーで PowerShell を開きます。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt huggingface-hub
.\.venv\Scripts\python.exe setup_models.py
.\.venv\Scripts\python.exe desktop.py
```

2 回目以降は、同じフォルダーで最後の起動コマンドを実行します。

## 資料を追加して使う

1. 左側でノートブックを作ります。
2. ファイルをウィンドウへドロップするか、右側の「ファイルを追加」から選びます。複数選択できます。
3. 質問に使う資料をチェックし、中央から送信します。回答の引用番号を押すと、右側に抽出した本文と位置が表示されます。
4. 要約するには、資料を選んで「選択中の資料を要約」を押すか、チャットに「要約して」と入力します。

取り込み・要約・回答の進捗は中央の会話欄に表示されます。回答や要約の途中でも資料を追加でき、追加分は次の質問から使えます。

| 資料 | できること |
| --- | --- |
| テキスト PDF、Word（.docx）、Excel（.xlsx）、PowerPoint（.pptx）、テキスト、CSV | 本文のプレビュー、質問、要約 |
| スキャン PDF | Windows の OCR で文字を読み取り、本文をプレビュー、質問、要約 |
| PNG・JPEG・WebP などの画像 | 画像モデルで見た目や読める文字を説明 |
| 動画など解析対象外のファイル | 添付として保管 |

原本は「元ファイルを保存」から取り出せます。スキャン PDF の読み取り精度は画像の鮮明さと、Windows にインストールされている OCR 言語に左右されます。

## 根拠レポートで原本を照合する

引用付き回答の下にある「根拠レポートを保存」を押すと、質問・回答・引用位置・抽出本文・原本の SHA-256 が一つの HTML にまとまります。受け取った人はブラウザーで開き、手元の資料と照合できます。

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '資料.pdf'
```

ハッシュが一致すれば、手元のファイルは保存時の原本と同じ内容です。回答が原文に沿っているかは、レポートが示す箇所を読んで確認できます。

## データの保存先と実行デバイス

ノートブック、原本、モデルはローカルに保存されます。標準の保存先は、旧版との互換性のため `%LOCALAPPDATA%\NotebookForAIPC` です。Microsoft Store 版 Python が別の場所に作った既存のノートブックも検出できます。

既定では対応する Intel GPU を優先し、使用できない場合は CPU で実行します。起動前に `$env:NOTEBOOK_DEVICE = 'CPU'` とすると CPU に固定できます。 `'NPU'` を指定した場合、資料検索用の処理で Intel NPU を優先します。回答・要約の生成には GPU または CPU を使います。

## 開発とライセンス

```powershell
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe make_source_release.py
```

公開用の `dist\Yomiquill-source.zip` にはアプリのソースと文書だけが入ります。ローカルで Windows 実行ファイルを検証する場合は、同じ仮想環境に `pyinstaller==6.22.3` を入れ、`.\build.ps1 -LocalOnly` を実行します。

ソースは [MIT ライセンス](LICENSE)です。依存ライブラリとモデルの条件は [第三者ライセンス](THIRD_PARTY_NOTICES.md)を参照してください。
