from __future__ import annotations

import re

_GRAM_AMOUNT = r"(?P<grams>[1-9][0-9]{0,8})[^\S\n]*(?:граммов|граммы|грамма|грамм|гр\.?|г\.?|grams?|g)"
_AMOUNT_FIRST = re.compile(rf"{_GRAM_AMOUNT}\s+(?P<name>.+)", re.IGNORECASE)
_AMOUNT_LAST = re.compile(rf"(?P<name>.+?)\s+{_GRAM_AMOUNT}", re.IGNORECASE)
_SEPARATOR = re.compile(r"[;\n]|(?<!\d),|,(?!\d)")
_WORDS = re.compile(r"[^\W\d_]+")
_AMBIGUOUS_WORDS = {"на", "до", "от", "около", "примерно", "плюс", "минус"}


def _words(name: str) -> list[str]:
    return _WORDS.findall(name.casefold().replace("ё", "е"))


def _food_head(word: str) -> str:
    # Allow a changed case ending, such as «каши» → «каша» or «риса» → «рис».
    return word[:-1] if len(word) >= 4 and word[-1] in "аяуыиео" else word


def _same_food(source: list[str], extracted: list[str]) -> bool:
    if not extracted:
        return False
    return _food_head(source[0]) == _food_head(extracted[0]) and (
        len(source) == 1 or len(extracted) == 1 or bool(set(source[1:]).intersection(extracted[1:]))
    )


def recover_explicit_food_weights(text: str, food_items: list[dict]) -> bool:
    """Fill missing weights when each item can be matched to one explicit amount."""
    if not any(item.get("quantity") is None or item.get("unit") is None for item in food_items):
        return False

    weights = match_explicit_food_weights(text, [item.get("name") for item in food_items])
    if weights is None:
        return False
    for item, grams in zip(food_items, weights):
        if item.get("quantity") is None or item.get("unit") is None:
            item["quantity"] = grams
            item["unit"] = "g"
    return True


def match_explicit_food_weights(text: str, names: list[object]) -> list[int] | None:
    """Return weights in the supplied dish order for an unambiguous text list."""
    text = re.sub(r"^\s*запиши(?:те)?\b[\s:]+", "", text, flags=re.IGNORECASE)
    fragments = [fragment.strip() for fragment in _SEPARATOR.split(text) if fragment.strip()]
    if len(fragments) != len(names):
        return None

    amounts = []
    for fragment in fragments:
        match = _AMOUNT_FIRST.fullmatch(fragment) or _AMOUNT_LAST.fullmatch(fragment)
        if match is None:
            return None
        name = match["name"]
        words = _words(name)
        if (
            not words
            or re.search(r"[\d:=+/~≈]", name)
            or words[0] in _AMBIGUOUS_WORDS
            or words[-1] in _AMBIGUOUS_WORDS
        ):
            return None
        amounts.append((words, int(match["grams"])))

    matches = []
    for name in names:
        if not isinstance(name, str):
            return None
        words = _words(name)
        candidates = [index for index, (source, _) in enumerate(amounts) if _same_food(source, words)]
        if len(candidates) != 1:
            return None
        matches.append(candidates[0])
    if len(set(matches)) != len(names):
        return None
    return [amounts[index][1] for index in matches]
