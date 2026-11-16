"""Create the reviewed source-only package. Run from any working directory."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


SOURCE_FILES = (
    ".gitignore", "LICENSE", "README.md", "THIRD_PARTY_NOTICES.md",
    "build.ps1", "desktop.py", "local_ai.py", "local_pdf.py", "local_vision.py",
    "make_source_release.py", "notebook_store.py", "requirements.txt",
    "setup_models.py", "test_notebook.py",
)


def main():
    root = Path(__file__).resolve().parent
    output = root / "dist" / "Yomiquill-source.zip"
    output.parent.mkdir(exist_ok=True)
    contents = {}
    for name in SOURCE_FILES:
        path = root / name
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"Expected a regular source file: {name}")
        contents[name] = path.read_text(encoding="utf-8").encode("utf-8")
    pending = output.with_suffix(".tmp")
    with ZipFile(pending, "w", ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(f"Yomiquill/{name}", data)
    pending.replace(output)
    print(output)


if __name__ == "__main__":
    main()
