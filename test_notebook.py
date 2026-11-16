import os
import subprocess
import sys
import tempfile
import unittest
import venv
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from shutil import copyfile
from unittest.mock import patch
from zipfile import ZipFile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from hardware_usage import adapter_loads
from local_ai import ModelNotInstalled, OpenVINOModel, data_home
from notebook_store import Notebook, NO_EVIDENCE, summarize_ocr_layout


def synthetic_training_pdf(path):
    """A four-page extraction fixture using a predefined CJK font, without embedded fonts."""
    writer = PdfWriter()
    name = NameObject
    font = DictionaryObject({
        name("/Type"): name("/Font"),
        name("/Subtype"): name("/Type0"),
        name("/BaseFont"): name("/HeiseiKakuGo-W5"),
        name("/Encoding"): name("/UniJIS-UCS2-H"),
    })
    pages = (
        "一般受講料は18,000円。会員団体の職員は13,500円。オンライン受講料は8,000円。出席率は80%以上。",
        "申込は開催月の前月までに受け付けます。",
        "早期の取消料は無料。期間内の取消料は受講料の50%。以降は100%。代理参加は無料。",
        "会場受講料とオンライン受講料の差額を返金します。",
    )
    for body in pages:
        page = writer.add_blank_page(595, 842)
        page[name("/Resources")] = DictionaryObject({name("/Font"): DictionaryObject({name("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 10 Tf 40 790 Td <{body.encode('utf-16-be').hex()}> Tj ET".encode("ascii"))
        page.replace_contents(stream)
    writer.write(path)


class Model:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def chat(self, prompt, structured=False):
        if structured:
            return {"answer": "受付は月曜日です。", "evidence": [{"source": 1, "quote": "受付は月曜日です。"}]}
        return prompt.splitlines()[-1]


class NotebookTest(unittest.TestCase):
    def test_hardware_usage_combines_processes_per_engine_and_separates_npu(self):
        gpu, npu = "0x00000000_0x000100a6", "0x00000000_0x000100b0"
        samples = [
            (f"pid_11_luid_{gpu}_phys_0_eng_0_engtype_3d", 28),
            (f"pid_12_luid_{gpu}_phys_0_eng_0_engtype_3d", 32),
            (f"pid_11_luid_{gpu}_phys_0_eng_1_engtype_compute", 41),
            (f"pid_11_luid_{npu}_phys_0_eng_0_engtype_compute", 37),
        ]
        self.assertEqual(adapter_loads(samples, {gpu: "GPU", npu: "NPU"}), {"GPU": 60, "NPU": 37})
        self.assertEqual(adapter_loads(samples, {gpu: "GPU"}), {"GPU": 60, "NPU": None})

    @unittest.skipUnless(
        sys.platform == "win32" and sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32,
        "Yomiquill launcher requires Windows and 64-bit Python 3.12",
    )
    def test_launcher_repairs_virtual_environment_without_pip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            python = root / ".venv" / "Scripts" / "python.exe"
            venv.EnvBuilder(with_pip=False).create(root / ".venv")
            copyfile(Path(__file__).with_name("start.py"), root / "start.py")
            # Exercise bootstrap itself without fetching wheels or model weights.
            (root / "requirements.txt").touch()
            (root / "setup_models.py").write_text("def prepare_models(): pass\n", encoding="utf-8")
            environment = {**os.environ, "PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": os.devnull}
            result = subprocess.run(
                [str(python), "-X", "utf8", str(root / "start.py"), "--setup-only"],
                cwd=root, env=environment, capture_output=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / ".venv" / ".yomiquill-requirements").is_file())

    def test_deferred_import_can_be_summarized_then_indexed_on_next_question(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "受付.txt"
            source.write_text("受付は月曜日です。", encoding="utf-8")
            model = Model()
            store = Notebook(root / "test.sqlite3", model)
            book = store.create("資料")
            with patch.object(model, "embed", wraps=model.embed) as embed:
                document = store.import_file(book, source, defer_index=True)
                self.assertEqual(store.summarize(book, document), "受付は月曜日です。")
                embed.assert_not_called()
                self.assertEqual(store.ask(book, "受付は何曜日？", [document]), "受付は月曜日です。")
                self.assertEqual(store.ask(book, "受付は何曜日？", [document]), "受付は月曜日です。")
                self.assertEqual(sum(call.args[0] == ["受付は月曜日です。"] for call in embed.call_args_list), 1)

    def test_pdf_example_summarizes_the_named_checked_document_with_page_references(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "XD3914.pdf"
            synthetic_training_pdf(pdf)
            other = root / "other.txt"
            other.write_text("別の資料です。", encoding="utf-8")
            model = Model()
            store = Notebook(root / "test.sqlite3", model)
            book = store.create("資料")
            document = store.import_file(book, pdf, defer_index=True)
            preferred = store.import_file(book, other, defer_index=True)
            question = "「XD3914.pdf」の要点と参照ページを教えて"
            with patch.object(model, "embed", side_effect=AssertionError("要約には検索を使わない")):
                self.assertNotEqual(store.ask(book, question, [preferred, document], preferred=preferred), NO_EVIDENCE)
                _, saved_question, _, refs = store.history(book)[-1]
                self.assertEqual(saved_question, question)
                self.assertTrue(refs)
                self.assertEqual(store.cited_documents(book, refs), {document})
                self.assertEqual(store.citation(book, refs[0])[1], "1 ページ")
                self.assertEqual(store.ask(book, question, [preferred]), NO_EVIDENCE)
                self.assertNotEqual(store.ask(book, "何ができるのか？", [document]), NO_EVIDENCE)

    def test_ocr_summary_preserves_section_boundaries_and_diagram_labels(self):
        headings = (
            "見出し（コントラスト補正）\n"
            "LED [ 10 >< 10 ] ドットマトリクス\n\n"
            "[ 1 6 >< 1 6 ] ドットマトリクス\n高輝度 2 色 ( 赤・黄緑 )"
            "\n\nページ全体\n"
        )
        _, first, second = summarize_ocr_layout(headings).splitlines()
        self.assertIn("10×10", first)
        self.assertNotIn("2 色", first)
        self.assertIn("16×16", second)
        self.assertIn("2 色", second)
        diagram = "Internal connection diagram\nRowl\nRow16\nColumn 1\nColumn 16\n64.0MAX\n20.0\nRed\nyellow-geen"
        summary = summarize_ocr_layout(diagram)
        self.assertIn("64.0 MAX", summary)
        self.assertIn("Column 1 と Column 16", summary)
        self.assertNotIn("20.0", summary)

    def test_existing_scan_and_image_can_be_summarized_without_reimporting(self):
        pdf = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(240, 240)
        writer.write(pdf)
        image = b"\x89PNG\r\n\x1a\nexisting-image"
        with tempfile.TemporaryDirectory() as folder:
            store = Notebook(Path(folder) / "test.sqlite3", Model())
            book = store.create("資料")
            with store.database() as db:
                for name, data in (("scan.pdf", pdf.getvalue()), ("ステッカー.png", image)):
                    key = db.execute(
                        "INSERT INTO documents(notebook, name) VALUES (?, ?)", (book, name)
                    ).lastrowid
                    db.execute("INSERT INTO originals VALUES (?, ?)", (key, data))
            self.assertEqual(store.document_states(book), {1: "scan", 2: "image"})
            with patch("local_vision.read_image_text", return_value="LEDモジュール"):
                self.assertEqual(store.ask(book, "要約してください。", [1]), "LEDモジュール")
            ref, = store.history(book)[-1][3]
            self.assertEqual(store.citation(book, ref)[1], "1 ページ（OCR）")
            self.assertEqual(store.document_states(book), {1: "ocr", 2: "image"})
            self.assertIsNone(store.image_preview(book, 1))
            self.assertEqual(store.image_preview(book, 2), image)
            with patch("local_vision.image_description", return_value="洋ナシのイラスト") as describe:
                question = "ステッカーについて説明して"
                self.assertEqual(store.ask(book, question, [1, 2]), "ステッカー.png\n洋ナシのイラスト")
                describe.assert_called_once_with(image, Path(folder) / "models" / "vision", question=question)
            self.assertEqual(store.original(book, 1), pdf.getvalue())

    def test_text_import_does_not_need_embedding_model_for_summary(self):
        class SummaryOnly(Model):
            def embed(self, texts):
                raise ModelNotInstalled()

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "資料.txt"
            source.write_text("本文を保存しました。", encoding="utf-8")
            store = Notebook(root / "test.sqlite3", SummaryOnly())
            notebook = store.create("資料")
            document = store.import_file(notebook, source)
            self.assertEqual(store.summarize(notebook, document), "本文を保存しました。")

    def test_packaged_data_home_prefers_existing_store_python_notebook(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"LOCALAPPDATA": folder}):
            local = Path(folder)
            legacy = local / "Packages" / "PythonSoftwareFoundation.Python.3.12_test"
            legacy = legacy / "LocalCache" / "Local" / "NotebookForAIPC"
            legacy.mkdir(parents=True)
            (legacy / "notebook.sqlite3").touch()
            self.assertEqual(data_home(), legacy)
            canonical = local / "NotebookForAIPC"
            canonical.mkdir()
            (canonical / "notebook.sqlite3").touch()
            self.assertEqual(data_home(), canonical)

    def test_import_text_office_and_attachment_preserves_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Notebook(root / "data.db", Model())
            book = store.create("資料")
            other = store.create("別のノート")
            text = root / "拡張子なし"
            text.write_text("受付は月曜日です。", encoding="utf-8")
            office = root / "資料.docx"
            with ZipFile(office, "w") as archive:
                archive.writestr("word/document.xml", "<document><p><t>研修案内</t></p></document>")
            binary = root / "添付.bin"
            binary.write_bytes(b"\x00\xff\x01")
            for source, expected in [(text, "受付は月曜日です。"), (office, "研修案内"), (binary, None)]:
                with self.subTest(source=source.name):
                    document = store.import_file(book, source)
                    self.assertEqual(store.original(book, document), source.read_bytes())
                    self.assertIsNone(store.original(other, document))
                    rows = store.document_text(book, document)
                    self.assertEqual(rows, [("1 行", expected)] if expected else [])
                    store.delete_document(book, document)
                    self.assertIsNone(store.original(book, document))

    def test_npu_preference_is_for_embedding_and_falls_back_after_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"NOTEBOOK_DEVICE": "NPU"}):
            root = Path(folder)
            for name in ("embedding", "chat"):
                (root / name).mkdir()
                (root / name / "openvino_model.bin").touch()
            model = OpenVINOModel(root)

            def create(path, device):
                return device

            def lose_npu(pipeline, device):
                if device == "NPU":
                    raise RuntimeError("NPU unavailable")
                return pipeline

            with patch("openvino.Core") as core:
                core.return_value.available_devices = ["CPU", "GPU.0", "NPU"]
                core.return_value.get_property.return_value = "Intel Arc Graphics"
                self.assertEqual(model.infer("embedding", create, lambda pipeline, device: pipeline), "NPU")
                self.assertEqual(model.infer("chat", create, lambda pipeline, device: pipeline), "GPU")
                self.assertEqual(model.infer("embedding", create, lose_npu), "GPU")
                self.assertEqual(model.devices, {"embedding": "GPU", "chat": "GPU"})

    def test_money_calculations_include_all_operand_and_rule_pages(self):
        change = {"source": 4, "quote": "会場受講料とオンライン受講料の差額を返金します。"}
        outcomes = [
            {"operation": "difference", "operands": [2, 6], "evidence": []},
            {"operation": "difference", "operands": [2, 3], "evidence": [change]},
            {"operation": "percent", "operands": [2, 99], "evidence": []},
        ]

        class EvidenceModel(Model):
            def chat(self, prompt, structured=False):
                return outcomes.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            store = Notebook(Path(directory) / "data.db", EvidenceModel())
            book = store.create("研修")
            fixture = Path(directory) / "研修実施要領.pdf"
            synthetic_training_pdf(fixture)
            document = store.import_file(book, fixture)
            cases = [
                ("会員団体の職員が会場受講を12月7日10時に取り消した場合、取消料はいくらですか？",
                 "6,750 円", ["1 ページ", "3 ページ"]),
                ("主催者判断でオンラインに切り替わる場合、会員の会場受講者にはいくら返金されますか？",
                 "5,500 円", ["1 ページ", "4 ページ"]),
                ("主催者判断でオンラインに切り替わる場合、会員の会場受講者への返金はいくらですか？",
                 NO_EVIDENCE, []),
            ]
            for question, expected, pages in cases:
                with self.subTest(question=question):
                    self.assertIn(expected, store.ask(book, question, [document]))
                    cites = store.history(book)[-1][3]
                    self.assertEqual([store.citation(book, cite)[1] for cite in cites], pages)

    def test_evidence_report_verifies_original_and_escapes_document_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "資料.txt"
            original = b"Evidence <script>alert(1)</script>"
            source.write_bytes(original)
            store = Notebook(root / "data.db", Model())
            book = store.create("資料")
            other = store.create("別の資料")
            document = store.import_file(book, source)
            with store.database() as db:
                reference, = db.execute("SELECT id FROM passages WHERE document = ?", (document,)).fetchone()
            store.save_answer(book, "What <now>?", "See document.", [reference])
            message = store.history(book)[-1][0]
            report = store.evidence_report(book, message)
            self.assertIn(sha256(original).hexdigest(), report)
            self.assertIn("資料.txt", report)
            self.assertIn("&lt;script&gt;", report)
            self.assertNotIn("<script>", report)
            with self.assertRaises(ValueError):
                store.evidence_report(other, message)
            store.delete_document(book, document)
            self.assertIn("引用元が削除されています", store.evidence_report(book, message))

    def test_import_answer_delete_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "受付.md"
            source.write_text("受付は月曜日です。", encoding="utf-8")
            app = Notebook(root / "data.db", Model())
            first = app.create("受付")
            second = app.create("別の資料")
            document = app.import_file(first, source)
            self.assertEqual(app.ask(second, "受付はいつ？", [document]), NO_EVIDENCE)
            self.assertEqual(app.ask(first, "受付はいつ？", []), NO_EVIDENCE)
            self.assertEqual(app.ask(first, "受付はいつ？", [document]), "受付は月曜日です。")
            reopened = Notebook(root / "data.db", Model())
            citation = reopened.history(first)[-1][3][0]
            self.assertEqual(reopened.citation(first, citation)[2], "受付は月曜日です。")
            self.assertEqual(reopened.cited_documents(first, [citation]), {document})
            self.assertEqual(reopened.cited_documents(second, [citation]), set())
            reopened.delete_document(first, document)
            self.assertIsNone(reopened.citation(first, citation))
            self.assertEqual(reopened.cited_documents(first, [citation]), set())

    def test_invalid_citation_is_not_saved_as_evidence(self):
        class InvalidCitation(Model):
            def chat(self, prompt, structured=False):
                return {"answer": "受付は月曜日です。", "evidence": [{"source": 99, "quote": "受付は月曜日です。"}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "受付.txt"
            source.write_text("受付は月曜日です。", encoding="utf-8")
            store = Notebook(root / "data.db", InvalidCitation())
            book = store.create("受付")
            document = store.import_file(book, source)
            self.assertEqual(store.ask(book, "受付はいつ？", [document]), NO_EVIDENCE)
            self.assertEqual(store.history(book)[-1][3], [])

    def test_import_failure_leaves_no_document(self):
        class FailedEmbedding(Model):
            def embed(self, texts):
                raise RuntimeError("失敗")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.txt"
            source.write_text("本文", encoding="utf-8")
            store = Notebook(root / "data.db", FailedEmbedding())
            book = store.create("資料")
            with self.assertRaises(RuntimeError):
                store.import_file(book, source)
            self.assertEqual(store.documents(book), [])


if __name__ == "__main__":
    unittest.main()
