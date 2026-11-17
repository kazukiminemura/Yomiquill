"""Define model response schemas and verify citations and calculations."""

import re
from decimal import Decimal

from question_intent import canonical


MONEY_QUESTION = re.compile(r"いくら|幾ら|何円|何万円|金額|料金を計算|(?:返金|支払|請求)額")
MONEY_VALUE = re.compile(r"(?<![\d,.])([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)(万円|千円|円|%)|無料")


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
