import codecs
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from decimal import Decimal
from hashlib import sha256
from html import escape
from io import BytesIO
from math import dist, isfinite, sqrt
from pathlib import Path
from unicodedata import normalize
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PyPdfError


from local_ai import ModelNotInstalled, OpenVINOModel
from local_vision import IMAGE_EXTENSIONS


NO_EVIDENCE = "選択した資料からは確認できません。"
MONEY_QUESTION = re.compile(r"いくら|幾ら|何円|何万円|金額|料金を計算|(?:返金|支払|請求)額")
MONEY_VALUE = re.compile(r"(?<![\d,.])([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)(万円|千円|円|%)|無料")
VISUAL_QUESTION = re.compile(r"画像|写真|イラスト|ステッカー|絵柄|マスコット|見た目")
SUMMARY_REQUEST = re.compile(
    r"(?:(?:この)?(?:資料|文書|ファイル|画像|内容)(?:の内容)?を)?"
    r"(?:要約(?:して(?:ください)?|をお願い(?:します)?|お願いします)?|"
    r"まとめて(?:ください)?|概要(?:を(?:教えて(?:ください)?|お願い(?:します)?))?|"
    r"内容を(?:説明して|教えて)(?:ください)?)"
)
OCR_HEADINGS = "見出し（コントラスト補正）\n"
OCR_BODY = "\n\nページ全体\n"
MATRIX_DIMENSIONS = re.compile(r"\[\s*([0-9][0-9 \t]*)\s*(?:><|[xX×])\s*([0-9][0-9 \t]*)\s*\]")


def object_schema(**properties):
    return {"type": "object", "properties": properties, "required": list(properties)}


TEXT = {"type": "string"}
REFERENCE = {"source": {"type": "integer"}, "quote": TEXT}
EVIDENCE = {"type": "array", "items": object_schema(**REFERENCE)}
ANSWER = object_schema(answer=TEXT, evidence=EVIDENCE)
CALCULATION = object_schema(
    evidence=EVIDENCE,
    operation={"enum": ["none", "amount", "percent", "difference", "sum"]},
    operands={"type": "array", "items": {"type": "integer"}},
)


def canonical(text):
    return "".join(normalize("NFKC", text).split())


def is_summary_request(question):
    return SUMMARY_REQUEST.fullmatch(canonical(question).rstrip("。.!！?？")) is not None


def readable_ocr(text):
    # Diagram dots and standalone numbers don't establish which labels they belong to.
    useful_lines = [
        line for line in text.splitlines()
        if re.search(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff]", line)
    ]
    text = MATRIX_DIMENSIONS.sub(
        lambda match: "×".join("".join(part.split()) for part in match.groups()),
        "\n".join(useful_lines),
    )
    return re.sub(r"(?<=[ァ-ヶ])[ \t]*[‐-―−][ \t]*(?=[ァ-ヶ])", "ー", text)


def summarize_ocr_layout(text):
    if text.startswith(OCR_HEADINGS) and OCR_BODY in text:
        heading_text, text = text.removeprefix(OCR_HEADINGS).split(OCR_BODY, 1)
        headings = []
        for heading in heading_text.split("\n\n"):
            lines = []
            for line in readable_ocr(heading).splitlines():
                dimension = re.search(r"\b\d+×\d+\b", line)
                if dimension:
                    # Keep the clear dimension and title, along with readable acronyms.
                    acronyms = re.findall(r"\b[A-Z]{2,}\b", line[:dimension.start()])
                    line = " ".join([*acronyms, line[dimension.start():]])
                if re.search(r"[A-Za-z]{3}|[ァ-ヶー]{3}|[一-龯]{2}|[A-Z]{2}\d", line):
                    lines.append(line.strip(" ,"))
            if lines:
                headings.append(" / ".join(lines))
        if len(headings) > 1:
            items = [f"{number}. {heading}" for number, heading in enumerate(headings, 1)]
            return "OCRで読み取れた見出しを区画ごとにまとめました。\n" + "\n".join(items)

    if not re.search(r"\b(?:connection|wiring|circuit) diagram\b", text, re.I):
        return None
    axes = []
    for axis in ("Row", "Column"):
        labels = sorted({int(number) for number in re.findall(rf"\b{axis}[ \t]*(\d+)\b", text, re.I)})
        if not labels:
            return None
        endpoints = (labels[0], labels[-1]) if len(labels) > 1 else labels
        axes.append(" と ".join(f"{axis} {number}" for number in endpoints))
    summary = ["接続図です。図中の行・列には " + "、".join(axes) + " の表記があります。"]
    maximum = re.search(r"\b(\d+(?:\.\d+)?)[ \t]*MAX\b", text, re.I)
    if maximum:
        summary.append(f"図には {maximum[1]} MAX の寸法表記があります。")
    colors = []
    if re.search(r"\bred\b", text, re.I):
        colors.append("Red（赤）")
    if re.search(r"\byellow[ -]+gr?een\b", text, re.I):
        colors.append("Yellow-green（黄緑）")
    if colors:
        summary.append("色の表記は " + "、".join(colors) + " です。")
    return "\n".join(summary)


def file_kind(name, header):
    if header.startswith(b"%PDF-"):
        return ".pdf"
    if (
        header.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM"))
        or header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    ):
        return ".png"
    return Path(name).suffix.lower()


def reference(entry, passages):
    if not isinstance(entry, dict):
        return None
    source = entry.get("source")
    quote = entry.get("quote")
    if type(source) is not int or not 1 <= source <= len(passages) or not isinstance(quote, str):
        return None
    fragment = canonical(quote)
    if len(fragment) < 3 or fragment not in canonical(passages[source - 1][1]):
        return None
    return source


def money_candidates(passages):
    candidates = []
    for source, (_, body, _) in enumerate(passages, 1):
        for sentence in re.split("[。！？]", canonical(body)):
            for match in MONEY_VALUE.finditer(sentence):
                number, unit = match.groups()
                amount = Decimal(number.replace(",", "")) if number else Decimal(0)
                amount *= {"万円": 10_000, "千円": 1_000}.get(unit, 1)
                context = sentence[max(0, match.start() - 80):match.end() + 50]
                candidates.append((source, amount, "%" if unit == "%" else "円", context))
    return candidates


def money_answer(result, passages, candidates):
    operation = result.get("operation")
    operands = result.get("operands")
    evidence = result.get("evidence")
    if operation not in ("amount", "percent", "difference", "sum"):
        return None
    if not isinstance(operands, list) or len(operands) != (1 if operation == "amount" else 2):
        return None
    if not isinstance(evidence, list):
        return None
    if any(type(key) is not int or not 1 <= key <= len(candidates) for key in operands):
        return None
    sources = [reference(entry, passages) for entry in evidence]
    if None in sources:
        return None
    values = []
    for key in operands:
        source, amount, unit, _ = candidates[key - 1]
        sources.append(source)
        values.append((amount, unit))

    left, left_unit = values[0]
    if left_unit != "円":
        return None
    if operation == "amount":
        return f"金額は {left:,} 円です。", sources
    right, right_unit = values[1]
    # A sourced percentage is a rate. Only two currency operands need an arithmetic label.
    if right_unit == "%":
        amount, expression = left * right / 100, f"{left:,} 円 × {right:,}%"
    elif operation == "difference" and evidence:
        amount, expression = left - right, f"{left:,} 円 − {right:,} 円"
    elif operation == "sum":
        amount, expression = left + right, f"{left:,} 円 ＋ {right:,} 円"
    else:
        return None
    if amount < 0:
        return None
    return f"金額は {amount:,} 円です（{expression}）。", sources


def text_chunks(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    line = 1
    for start in range(0, len(text), 800):
        segment = text[start:start + 900]
        end = line + segment.rstrip("\n").count("\n")
        if segment.strip():
            yield f"{line} 行" if line == end else f"{line}–{end} 行", segment
        line += text.count("\n", start, start + 800)


def decode_text(data, kind):
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encodings = ("utf-32",)
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ("utf-16",)
    elif kind in {".txt", ".md", ".markdown", ".csv", ".tsv", ".log"}:
        encodings = ("utf-8-sig", "cp932")
    else:
        encodings = ("utf-8-sig",)
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        if all(char.isprintable() or char in "\r\n\t\f" for char in text):
            return text
    return ""


def office_text(data, kind):
    with ZipFile(BytesIO(data)) as archive:
        remaining = 32 * 1024 * 1024

        def xml(name):
            nonlocal remaining
            remaining -= archive.getinfo(name).file_size
            if remaining < 0:
                raise ValueError("Office 文書の本文が大きすぎます。")
            return ElementTree.fromstring(archive.read(name))

        if kind == ".docx":
            for paragraph in xml("word/document.xml").iterfind(".//{*}p"):
                yield "".join(node.text or "" for node in paragraph.iterfind(".//{*}t"))
        elif kind == ".pptx":
            slides = [
                name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ]
            for name in sorted(slides, key=lambda name: int(Path(name).stem[5:])):
                for paragraph in xml(name).iterfind(".//{*}p"):
                    yield "".join(node.text or "" for node in paragraph.iterfind(".//{*}t"))
        else:
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = [
                    "".join(node.text or "" for node in item.iterfind(".//{*}t"))
                    for item in xml("xl/sharedStrings.xml").iterfind("{*}si")
                ]
            sheets = [
                name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            ]
            for name in sorted(sheets, key=lambda name: int(Path(name).stem[5:])):
                for row in xml(name).iterfind(".//{*}sheetData/{*}row"):
                    values = []
                    for cell in row.iterfind("{*}c"):
                        value = cell.findtext("{*}v", "")
                        if cell.get("t") == "s":
                            value = shared[int(value)]
                        elif cell.get("t") == "inlineStr":
                            value = "".join(node.text or "" for node in cell.iterfind(".//{*}t"))
                        values.append(value)
                    yield "\t".join(values)


def extract_passages(data, kind):
    if kind == ".pdf" or data.startswith(b"%PDF-"):
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                return []
            chunks = []
            for page, content in enumerate(reader.pages, 1):
                text = (content.extract_text() or "").strip()
                chunks.extend(
                    (f"{page} ページ", text[start:start + 900])
                    for start in range(0, len(text), 800)
                )
            return chunks
        except (PyPdfError, ValueError, NotImplementedError):
            return []
    if kind in {".docx", ".pptx", ".xlsx"}:
        try:
            return list(text_chunks("\n".join(office_text(data, kind))))
        except (BadZipFile, ElementTree.ParseError, KeyError, IndexError, ValueError, RuntimeError):
            return []
    return list(text_chunks(decode_text(data, kind)))


def normalized(vector):
    length = sqrt(sum(value * value for value in vector))
    if not length or not isfinite(length):
        raise ValueError("検索用ベクトルを生成できませんでした。")
    return [value / length for value in vector]


class Notebook:
    def __init__(self, path: Path, model=None):
        self.path = path
        self.model = model if model is not None else OpenVINOModel(path.parent / "models")
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS notebooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notebook INTEGER NOT NULL REFERENCES notebooks ON DELETE CASCADE,
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS passages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document INTEGER NOT NULL REFERENCES documents ON DELETE CASCADE,
                    location TEXT NOT NULL, body TEXT NOT NULL, vector TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS originals (
                    document INTEGER PRIMARY KEY REFERENCES documents ON DELETE CASCADE,
                    content BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notebook INTEGER NOT NULL REFERENCES notebooks ON DELETE CASCADE,
                    question TEXT NOT NULL, answer TEXT NOT NULL, citations TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS documents_notebook ON documents(notebook);
                CREATE INDEX IF NOT EXISTS passages_document ON passages(document);
                CREATE INDEX IF NOT EXISTS messages_notebook ON messages(notebook);
            """)

    @contextmanager
    def database(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def list(self):
        with self.database() as db:
            return db.execute("SELECT id, name FROM notebooks ORDER BY id").fetchall()

    def create(self, name):
        with self.database() as db:
            return db.execute("INSERT INTO notebooks(name) VALUES (?)", (name,)).lastrowid

    def rename(self, notebook, name):
        with self.database() as db:
            db.execute("UPDATE notebooks SET name = ? WHERE id = ?", (name, notebook))

    def delete(self, notebook):
        with self.database() as db:
            db.execute("DELETE FROM notebooks WHERE id = ?", (notebook,))

    def documents(self, notebook):
        with self.database() as db:
            return db.execute(
                "SELECT id, name FROM documents WHERE notebook = ? ORDER BY id", (notebook,)
            ).fetchall()

    def import_file(self, notebook, path, *, defer_index=False):
        data = path.read_bytes()
        kind = file_kind(path.name, data[:16])
        chunks = [] if kind in IMAGE_EXTENSIONS else extract_passages(data, kind)
        if kind == ".pdf" and not chunks:
            try:
                chunks = self.scan_pdf(data)
            except (ValueError, RuntimeError):
                # Keep the original and allow retry when OCR is available.
                pass
        rows = [(location, body, "[]") for location, body in chunks] if defer_index else self.index_passages(chunks)
        with self.database() as db:
            document = db.execute(
                "INSERT INTO documents(notebook, name) VALUES (?, ?)", (notebook, path.name)
            ).lastrowid
            db.execute("INSERT INTO originals VALUES (?, ?)", (document, data))
            self.insert_passages(db, document, rows)
            return document

    def index_passages(self, chunks):
        vectors = []
        try:
            for start in range(0, len(chunks), 16):
                batch = [body for _, body in chunks[start:start + 16]]
                vectors.extend(self.model.embed(batch))
        except ModelNotInstalled:
            return [(location, body, "[]") for location, body in chunks]
        return [
            (location, body, json.dumps(normalized(vector)))
            for (location, body), vector in zip(chunks, vectors, strict=True)
        ]

    @staticmethod
    def insert_passages(db, document, rows):
        db.executemany(
            "INSERT INTO passages(document, location, body, vector) VALUES (?, ?, ?, ?)",
            [(document, *row) for row in rows],
        )

    def document_states(self, notebook):
        with self.database() as db:
            rows = db.execute("""
                SELECT d.id, d.name, substr(o.content, 1, 16),
                    (SELECT location FROM passages WHERE document = d.id ORDER BY id LIMIT 1)
                FROM documents d LEFT JOIN originals o ON o.document = d.id
                WHERE d.notebook = ? ORDER BY d.id
            """, (notebook,)).fetchall()
        states = {}
        for key, name, header, first in rows:
            kind = file_kind(name, header or b"")
            if first:
                state = "ocr" if first.endswith("（OCR）") else "text"
            elif header is not None and kind in IMAGE_EXTENSIONS:
                state = "image"
            elif header is not None and kind == ".pdf":
                state = "scan"
            else:
                state = "attachment"
            states[key] = state
        return states

    def image_preview(self, notebook, document):
        with self.database() as db:
            row = db.execute("""
                SELECT d.name, o.content FROM originals o JOIN documents d ON d.id = o.document
                WHERE d.notebook = ? AND d.id = ? AND length(o.content) <= 20 * 1024 * 1024
            """, (notebook, document)).fetchone()
        if row and file_kind(row[0], row[1][:16]) in IMAGE_EXTENSIONS:
            return row[1]
        return None

    def scan_pdf(self, data):
        from local_pdf import dark_heading_images, rendered_pages
        from local_vision import read_image_text

        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise ValueError("パスワード保護された PDF は、解除したコピーを追加してください。")
            pages = range(1, len(reader.pages) + 1)
        except PyPdfError as error:
            raise ValueError("PDF を開けませんでした。ファイルの破損を確認してください。") from error
        chunks = []
        for number, image in rendered_pages(data, pages):
            text = read_image_text(image).strip()
            headings = [read_image_text(band).strip() for band in dark_heading_images(image)]
            headings = [heading for heading in headings if len(heading) >= 12]
            if headings:
                text = OCR_HEADINGS + "\n\n".join(headings) + OCR_BODY + text
            for start in range(0, len(text), 800):
                chunks.append((f"{number} ページ（OCR）", text[start:start + 900]))
        if not chunks:
            raise ValueError("PDF から文字を読み取れませんでした。文字が鮮明な資料で試してください。")
        return chunks

    def reprocess_document(self, notebook, document):
        state = self.document_states(notebook).get(document)
        if state == "ocr":
            return
        data = self.original(notebook, document)
        if not data or state != "scan":
            raise ValueError("読み取り直せる PDF が見つかりません。")
        rows = self.index_passages(self.scan_pdf(data))
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM passages WHERE document = ?", (document,)).fetchone():
                self.insert_passages(db, document, rows)

    def has_original(self, notebook, document):
        with self.database() as db:
            return db.execute("""
                SELECT 1 FROM originals o JOIN documents d ON d.id = o.document
                WHERE d.notebook = ? AND d.id = ?
            """, (notebook, document)).fetchone() is not None

    def original(self, notebook, document):
        with self.database() as db:
            row = db.execute("""
                SELECT o.content FROM originals o JOIN documents d ON d.id = o.document
                WHERE d.notebook = ? AND d.id = ?
            """, (notebook, document)).fetchone()
            return row[0] if row else None

    def delete_document(self, notebook, document):
        with self.database() as db:
            db.execute("DELETE FROM documents WHERE notebook = ? AND id = ?", (notebook, document))

    def citation(self, notebook, passage):
        with self.database() as db:
            return db.execute("""
                SELECT d.name, p.location, p.body FROM passages p
                JOIN documents d ON p.document = d.id WHERE d.notebook = ? AND p.id = ?
            """, (notebook, passage)).fetchone()

    def cited_documents(self, notebook, passages):
        ids = set(passages)
        if not ids:
            return set()
        with self.database() as db:
            rows = db.execute("""
                SELECT p.id, d.id FROM passages p
                JOIN documents d ON d.id = p.document
                WHERE d.notebook = ? AND p.id IN (SELECT value FROM json_each(?))
            """, (notebook, json.dumps(list(ids)))).fetchall()
        return {document for _, document in rows} if len(rows) == len(ids) else set()

    def history(self, notebook, limit=-1):
        with self.database() as db:
            rows = db.execute(
                "SELECT id, question, answer, citations FROM messages WHERE notebook = ?"
                " ORDER BY id DESC LIMIT ?", (notebook, limit)
            ).fetchall()
        return [(key, question, answer, json.loads(refs)) for key, question, answer, refs in reversed(rows)]

    def evidence_report(self, notebook, message):
        with self.database() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT question, answer, citations FROM messages WHERE notebook = ? AND id = ?",
                (notebook, message),
            ).fetchone()
            if row is None or not (references := json.loads(row[2])):
                raise ValueError("根拠のある回答が見つかりません。")
            question, answer, _ = row
            hashes = {}
            evidence = []
            for passage in references:
                source = db.execute("""
                    SELECT d.id, d.name, p.location, p.body, o.document
                    FROM passages p JOIN documents d ON p.document = d.id
                    LEFT JOIN originals o ON o.document = d.id
                    WHERE d.notebook = ? AND p.id = ?
                """, (notebook, passage)).fetchone()
                if source is None:
                    evidence.append("<li>引用元が削除されています。</li>")
                    continue
                key, name, location, body, original = source
                if original is not None and key not in hashes:
                    digest = sha256()
                    with db.blobopen("originals", "content", key, readonly=True) as data:
                        for chunk in iter(lambda: data.read(1024 * 1024), b""):
                            digest.update(chunk)
                    hashes[key] = digest.hexdigest()
                fingerprint = hashes.get(key, "原本が保存されていません")
                evidence.append(
                    f"<li><h3>{escape(name)} · {escape(location)}</h3>"
                    f"<p>原本 SHA-256 <code>{fingerprint}</code></p><pre>{escape(body)}</pre></li>"
                )
        return (
            '<!doctype html><html lang="ja"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Yomiquill 根拠レポート</title>'
            '<style>body{max-width:52rem;margin:2rem auto;padding:0 1rem;font:1rem/1.6 system-ui}'
            'pre{white-space:pre-wrap;font:inherit;padding:1rem;background:#f2f4f8}'
            'code{overflow-wrap:anywhere}</style>'
            f'<main><h1>根拠レポート</h1><h2>質問</h2><pre>{escape(question)}</pre>'
            f'<h2>回答</h2><pre>{escape(answer)}</pre><h2>引用した資料</h2>'
            '<p>抽出した本文と位置を記載しています。SHA-256は手元のファイルと原本が同じかを'
            '照合するための値です。</p>'
            f'<ol>{"".join(evidence)}</ol></main></html>'
        )

    def save_answer(self, notebook, question, answer, citations):
        with self.database() as db:
            db.execute(
                "INSERT INTO messages(notebook, question, answer, citations) VALUES (?, ?, ?, ?)",
                (notebook, question, answer, json.dumps(citations)),
            )
        return answer

    def ask(self, notebook, question, selected):
        names = dict(self.documents(notebook))
        states = self.document_states(notebook)
        sources = [key for key in dict.fromkeys(selected) if states.get(key) in {"text", "ocr", "image", "scan"}]
        if is_summary_request(question) and sources:
            return self.answer_sources(notebook, question, sources)
        images = [key for key in sources if states[key] == "image"]
        query_name = canonical(question).casefold()
        matched = [key for key in sources if canonical(Path(names[key]).stem).casefold() in query_name]
        matched_images = [key for key in matched if states[key] == "image"]
        if matched_images or (images and (sources == images or not matched and VISUAL_QUESTION.search(question))):
            return self.answer_sources(notebook, question, matched_images or images, visual_question=question)
        for key in matched or sources:
            if states[key] == "scan":
                try:
                    self.reprocess_document(notebook, key)
                except (ValueError, RuntimeError):
                    if len(matched or sources) == 1:
                        raise

        with self.database() as db:
            rows = db.execute("""
                SELECT p.id, p.body, p.vector FROM passages p
                JOIN documents d ON p.document = d.id
                WHERE d.notebook = ? AND d.id IN (SELECT value FROM json_each(?))
            """, (notebook, json.dumps(matched or sources))).fetchall()
        if not rows:
            return self.save_answer(notebook, question, NO_EVIDENCE, [])
        available = {row[0] for row in rows}
        context = "\n".join(
            f"質問：{prior_question[:400]}\n回答：{answer[:400]}"
            for _, prior_question, answer, refs in self.history(notebook, limit=3)
            if refs and available.issuperset(refs)
        )
        query = question
        if context and (len(question) <= 24 or question.startswith(("その", "それ", "では", "この場合"))):
            query = self.model.chat(
                "最新の質問だけで意味が通る検索文を1文作ってください。必要な場合だけ以前の"
                "会話から指示語の対象を補い、回答は書かないでください。\n"
                f"以前の会話：{context}\n最新の質問：{question}"
            )[:1000] or question
        instruction = "Instruct: Given a question, retrieve passages that answer it\nQuery: "
        vector, = self.model.embed([instruction + query])
        vector = normalized(vector)
        missing = [(i, key, body) for i, (key, body, stored) in enumerate(rows) if stored == "[]"]
        for start in range(0, len(missing), 16):
            batch = missing[start:start + 16]
            embeddings = self.model.embed([body for _, _, body in batch])
            indexed = []
            for (i, key, body), embedding in zip(batch, embeddings, strict=True):
                vector_data = json.dumps(normalized(embedding))
                rows[i] = (key, body, vector_data)
                indexed.append((vector_data, key))
            with self.database() as db:
                db.executemany("UPDATE passages SET vector = ? WHERE id = ? AND vector = '[]'", indexed)
        # ponytail: scan the selected notebook in memory; add an index when this becomes slow.
        best = sorted(sorted(rows, key=lambda row: dist(vector, json.loads(row[2])))[:8])
        excerpts = "\n\n".join(f"資料番号 {i}\n{row[1]}" for i, row in enumerate(best, 1))
        clarification = f"質問の指示語の補足：{query}\n" if query != question else ""
        material = f"質問：{question}\n{clarification}\n資料：\n{excerpts}"
        if MONEY_QUESTION.search(question):
            candidates = money_candidates(best)
            choices = "\n".join(
                f"{key}. {amount:,}{unit} | 資料番号 {source} | {context}"
                for key, (source, amount, unit, context) in enumerate(candidates, 1)
            )
            response = self.model.chat(
                "質問の人物・料金区分・日時・事由を資料と照合します。"
                "会員なら会員料金を選び、一般料金は使いません。"
                "中止・切替・本人による取消は別の事由です。計算結果は書きません。\n"
                "最初に evidence に、この質問に該当する条件や金額の原文だけを引用します。"
                "各項目の source は資料番号、quote は短い原文です。\n"
                "operation は、記載額なら amount、料金×割合なら percent、料金の差額なら"
                " difference、合計なら sum、不明なら none。\n"
                "operands は、下の数値候補から選んだ候補番号の配列です。資料番号と区別します。"
                "差額なら元の料金、変更後の料金の順、割合なら料金、割合の順です。\n"
                + material + "\n\n数値候補：\n" + choices + "\n\n質問の対象：" + question,
                structured=CALCULATION,
            )
            verified = money_answer(response, best, candidates)
            answer, refs = verified if verified else (NO_EVIDENCE, [])
        else:
            response = self.model.chat(
                "資料で裏づけられる内容だけで質問に答えてください。回答全体を裏づけるため、"
                "各事実や条件に使った資料箇所をすべて evidence に入れてください。"
                "各要素に資料番号 source と、該当箇所の短い原文 quote を入れます。"
                "answer に資料番号は書かず、根拠が不足する場合は evidence を空にしてください。\n"
                + material,
                structured=ANSWER,
            )
            answer = response.get("answer")
            entries = response.get("evidence")
            refs = [reference(entry, best) for entry in entries] if isinstance(entries, list) else []
        if not isinstance(answer, str) or not answer.strip() or not refs or None in refs:
            return self.save_answer(notebook, question, NO_EVIDENCE, [])
        return self.save_answer(notebook, question, answer, [best[ref - 1][0] for ref in sorted(set(refs))])

    def document_text(self, notebook, document, limit=-1):
        with self.database() as db:
            return db.execute("""
                SELECT p.location, p.body FROM passages p
                JOIN documents d ON p.document = d.id WHERE d.notebook = ? AND d.id = ?
                ORDER BY p.id LIMIT ?
            """, (notebook, document, limit)).fetchall()

    def source_summary(self, notebook, document, question=None):
        state = self.document_states(notebook).get(document)
        if state == "image":
            from local_vision import image_description

            image = self.original(notebook, document)
            models = getattr(self.model, "models", self.path.parent / "models")
            return image_description(image, models / "vision", question=question), []
        if state == "scan":
            self.reprocess_document(notebook, document)
        with self.database() as db:
            rows = db.execute("""
                SELECT p.id, p.location, p.body FROM passages p
                JOIN documents d ON d.id = p.document
                WHERE d.notebook = ? AND d.id = ? ORDER BY p.id
            """, (notebook, document)).fetchall()
        if not rows:
            raise ValueError("この資料から要約できる本文を読み取れませんでした。")
        prompt = (
            "資料を日本語で200字程度に要約してください。資料の種類と、明記された重要な仕様・構成を"
            "挙げてください。複数の型番・製品・見出しがある場合は別々にまとめ、数値や色を"
            "別の製品に割り当てないでください。接続図では行列の範囲、色、寸法など確認できる"
            "構成を優先し、不鮮明な端子番号や読み取れない箇所は省略してください。"
            "数値と項目の関係が同じ行に明記されていない場合は結び付けないでください。"
            "資料にない用途・性能・評価を加えないでください。\n"
        )
        refs = {}
        for key, location, _ in rows:
            refs.setdefault(location, key)
        evidence = list(refs.values())[:6]
        if len(refs) == 1 and rows[0][1].endswith("（OCR）"):
            page_text = "".join(body[:800] for _, _, body in rows[:-1]) + rows[-1][2]
            extracted = summarize_ocr_layout(page_text)
            if extracted:
                return extracted, evidence
        level = [
            (readable_ocr(body) or body) if location.endswith("（OCR）") else body
            for _, location, body in rows
        ]
        while True:
            summaries = []
            for start in range(0, len(level), 4):
                summary = self.model.chat(prompt + "\n".join(level[start:start + 4]))
                if not summary:
                    raise RuntimeError("要約が空でした。再試行してください。")
                summaries.append(summary)
            if len(summaries) == 1:
                return summaries[0], evidence
            level = summaries

    def answer_sources(self, notebook, question, documents, visual_question=None):
        names = dict(self.documents(notebook))
        outputs = []
        citations = []
        failures = []
        for document in documents:
            name = names[document]
            try:
                summary, refs = self.source_summary(notebook, document, visual_question)
            except (RuntimeError, ValueError) as error:
                if len(documents) == 1:
                    raise
                failures.append(f"{name}\n{error}")
                continue
            if len(documents) > 1 or visual_question:
                summary = f"{name}\n{summary}"
            outputs.append(summary)
            citations.extend(refs)
        if not outputs:
            raise ValueError("\n\n".join(failures))
        if failures:
            outputs.append("読み取れなかった資料\n" + "\n\n".join(failures))
        return self.save_answer(notebook, question, "\n\n".join(outputs), citations)

    def summarize(self, notebook, document, question=None):
        name = dict(self.documents(notebook)).get(document)
        if name is None:
            raise ValueError("要約する資料が見つかりません。")
        label = "要約" if question is None or is_summary_request(question) else question
        return self.answer_sources(notebook, f"{label} · {name}", [document])
