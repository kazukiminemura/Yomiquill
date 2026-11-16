# Third-party components

The top-level MIT license applies to Yomiquill's project-owned source and documentation. The public source archive does not bundle Python, wheels, executable files, model weights, fonts, or user data. Install dependencies and download models separately from their original distributors. Their terms and notices apply to those downloads.

| Component | Upstream terms or notices |
| --- | --- |
| Python | [Python Software Foundation License and bundled notices](https://docs.python.org/3.12/license.html) |
| PySide6 Essentials, Shiboken6 and Qt | [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html), [Qt licensing and third-party components](https://doc.qt.io/qt-6/licensing.html) |
| pypdf | [BSD-3-Clause](https://github.com/py-pdf/pypdf/blob/main/LICENSE) |
| pypdfium2 and PDFium | [pypdfium2 and bundled component licenses](https://github.com/pypdfium2-team/pypdfium2#licensing) |
| OpenVINO, GenAI and Tokenizers | [OpenVINO](https://github.com/openvinotoolkit/openvino/blob/master/LICENSE), [GenAI](https://github.com/openvinotoolkit/openvino.genai/blob/master/LICENSE), [Tokenizers](https://github.com/openvinotoolkit/openvino_tokenizers/blob/master/LICENSE) |
| Hugging Face Hub | [Apache-2.0](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE) |
| PyInstaller (optional local build tool) | [PyInstaller license and distribution exception](https://pyinstaller.org/en/stable/license.html) |

Windows OCR uses the installed Windows component and installed language packs. The source archive includes only the code that calls them.

The OpenVINO-converted Qwen models are obtained directly through Hugging Face. The upstream cards list Apache-2.0 and identify the original model creators: [chat](https://huggingface.co/OpenVINO/Qwen3-4B-int4-ov), [embedding](https://huggingface.co/OpenVINO/Qwen3-Embedding-0.6B-int4-cw-ov), [vision](https://huggingface.co/OpenVINO/Qwen3-VL-4B-Instruct-int4-ov). Weights and downloaded tokenizer/configuration files are outside the Yomiquill MIT grant.

A frozen Windows build bundles third-party components and is for local testing. Before distributing such a build, inventory the exact binaries and transitive dependencies, include their respective notices, and satisfy the applicable Qt and other redistribution obligations for that build.
