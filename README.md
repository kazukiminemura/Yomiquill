# Yomiquill（ヨミクイル）

資料から引用付きの回答や要約を作り、根拠をあとから原本と照合できる、Windows x64向けの日本語デスクトップアプリです。
PythonとPySide6で画面を構成し、検索用の埋め込みと文章生成をOpenVINO GenAIで実行します。

回答の「根拠レポートを保存」から、質問・回答・引用位置・抽出本文・原本のSHA-256を一つのHTMLに保存できます。受け取った側はアプリやアカウントなしでHTMLを開き、手元の原本を `Get-FileHash -Algorithm SHA256 -LiteralPath '資料.pdf'` で照合できます。ハッシュが一致すればファイルの内容は同一です。回答が原文に沿っているかは、掲載された箇所を参照して確認してください。

## 初回セットアップ

64ビット版のPython 3.12を用意し、このディレクトリでPowerShellを開きます。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install huggingface-hub
.\.venv\Scripts\python.exe setup_models.py
```

初回は本文・検索・画像のモデルを合計約 5.8 GB 取得します。本文・検索モデルがある場合は、同じ手順で画像モデルを追加できます。モデル取得後はオフラインで使えます。
公開用ソースZIPを展開した場合や、リポジトリを取得した場合は、ソースフォルダーで続けて次のコマンドを実行します。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe desktop.py
```

モデルを手動で準備する場合は、[回答モデル](https://huggingface.co/OpenVINO/Qwen3-4B-int4-ov) を `models\chat`、
[検索モデル](https://huggingface.co/OpenVINO/Qwen3-Embedding-0.6B-int4-cw-ov) を `models\embedding`、
[画像モデル](https://huggingface.co/OpenVINO/Qwen3-VL-4B-Instruct-int4-ov) を `models\vision` に置きます。
Microsoft Store 版 Python が保存した既存のノートブックは、配布版からも開けます。

## 使い方

1. 左側でノートブックを作成します。
2. ウィンドウにファイルをドラッグ＆ドロップするか、右側の「ファイルを追加」から選びます。複数選択できます。質問に使う資料をチェックします。
3. 中央で質問を送ります。回答下の引用番号を押すと右側に原文と位置が表示されます。
4. 右側で資料を選択して「選択中の資料を要約」を押すか、チャットに「要約して」と入力します。

本文を検索・要約できるのは、テキストを選択できる PDF、Word（.docx）、Excel（.xlsx）、PowerPoint（.pptx）、UTF-8 テキストなどです。BOM 付きの UTF-16/32 と、TXT・CSV などの Windows 日本語テキストにも対応します。
画像だけの PDF は、Windows にインストール済みの OCR 言語を使って読み取ります。読み取り結果はプレビューで確認できます。
PNG・JPEG などの画像は、画像モデルで見た目や読める文字を説明できます。以前に添付した画像やスキャン PDF も、そのまま選択して要約できます。
動画など解析対象外の形式は添付ファイルとして保管されます。原本は「元ファイルを保存」から取り出せます。
データベースとモデルの保存先は、旧版との互換性のため `%LOCALAPPDATA%\NotebookForAIPC` です。
既定ではOpenVINOがIntel GPUを優先し、初期化または推論に失敗したモデルはCPUで再試行します。
CPUに固定する場合は、PowerShellで `$env:NOTEBOOK_DEVICE = 'CPU'` を設定してから起動します。
Intel NPUを資料検索に使う場合は、起動前に `$env:NOTEBOOK_DEVICE = 'NPU'` を設定してください。
NPUを利用できない場合、検索はGPU、次にCPUを試します。引用付き回答と要約の生成にはGPUまたはCPUを使用します。
NPUでの最初の検索や取り込みでは、モデルの準備に時間がかかる場合があります。画面下部に実際に使ったデバイスを表示します。

## テストと公開用ソース

```powershell
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe make_source_release.py
```

公開対象は `dist\Yomiquill-source.zip` です。モデル・依存ライブラリ・フォント・旧テストPDF・作業ダッシュボードを含みません。テストは架空の研修PDFを実行時に生成し、複数ページにまたがる料金計算と引用を検証します。
新しい公開リポジトリを作る場合は、このソースZIPを展開したフォルダーを最初のコミットに使ってください。旧リポジトリの履歴にはフォントを埋め込んだ旧テストPDFが残っています。

ローカルでWindows実行ファイルを試す場合は、次の手順で `dist\Yomiquill-local-win-x64.zip` を作成できます。このZIPはローカル検証用です。

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller==6.22.3
.\build.ps1 -LocalOnly
```

本プロジェクトのソースは [MITライセンス](LICENSE) です。依存関係とモデルについては [第三者ライセンス](THIRD_PARTY_NOTICES.md) を参照してください。
