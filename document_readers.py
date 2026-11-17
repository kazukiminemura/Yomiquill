"""Identify source formats and extract passages with their original locations."""

import codecs
import re
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PyPdfError


OCR_HEADINGS = "見出し（コントラスト補正）\n"
OCR_BODY = "\n\nページ全体\n"
MATRIX_DIMENSIONS = re.compile(r"\[\s*([0-9][0-9 \t]*)\s*(?:><|[xX×])\s*([0-9][0-9 \t]*)\s*\]")


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


def scan_pdf(data):
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
