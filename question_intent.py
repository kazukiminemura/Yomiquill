"""Recognize summary requests without depending on storage or the UI."""

import re
from dataclasses import dataclass
from unicodedata import normalize


SUMMARY_REQUEST = re.compile(
    r"(?:「(?P<filename>[^」]+)」の)?"
    r"(?:(?:この)?(?:資料|文書|ファイル|画像|内容)(?:の内容)?を)?"
    r"(?:要約(?:して(?:ください)?|をお願い(?:します)?|お願いします)?|"
    r"まとめて(?:ください)?|概要(?:を(?:教えて(?:ください)?|お願い(?:します)?))?|"
    r"内容を(?:説明して|教えて)(?:ください)?|"
    r"要点(?:と参照ページ)?を教えて(?:ください)?|"
    r"何ができる(?:のか|の)?)"
)


def canonical(text: str) -> str:
    return "".join(normalize("NFKC", text).split())


@dataclass(frozen=True)
class SummaryRequest:
    filename: str | None


def summary_request(question: str) -> SummaryRequest | None:
    match = SUMMARY_REQUEST.fullmatch(canonical(question).rstrip("。.!！?？"))
    if match is None:
        return None
    return SummaryRequest(filename=match["filename"])
