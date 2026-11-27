"""Conservative offline spelling and punctuation cleanup."""

import re
import unicodedata


COMMON_CORRECTIONS = {
    "teh": "the",
    "recieve": "receive",
    "seperate": "separate",
    "definately": "definitely",
    "dont": "don't",
    "doesnt": "doesn't",
    "cant": "can't",
    "wont": "won't",
    "isnt": "isn't",
    "im": "I'm",
    "ive": "I've",
    "gonna": "going to",
    "wanna": "want to",
    "couldnt": "couldn't",
    "shouldnt": "shouldn't",
    "wouldnt": "wouldn't",
    "नही": "नहीं",
    "achha": "accha",
    "aacha": "accha",
    "kese": "kaise",
    "nhi": "nahi",
    "krna": "karna",
    "plz": "please",
    "pls": "please",
    "thx": "thanks",
    "alot": "a lot",
}
WORD_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|[\u0900-\u097F]+", re.UNICODE)
SPACE_PATTERN = re.compile(r"[ \t]{2,}")


def _replace_word(match):
    word = match.group(0)
    if word.isupper():
        return word
    corrected = COMMON_CORRECTIONS.get(word.casefold())
    if corrected is None:
        return word
    if word[:1].isupper() and corrected[:1].islower():
        return corrected.capitalize()
    return corrected


def correct_text(text: str, enabled: bool = True) -> str:
    """Apply only known word fixes and light punctuation normalization."""
    if not enabled or not isinstance(text, str):
        return text
    result = WORD_PATTERN.sub(_replace_word, text)
    result = SPACE_PATTERN.sub(" ", result).strip()
    result = re.sub(r"\s+([,.;!?])", r"\1", result)
    result = re.sub(r"([!?.,]){2,}", lambda match: match.group(0)[0], result)
    last_base = next((character for character in reversed(result) if unicodedata.category(character)[0] in "LN"), "")
    if last_base and not result.rstrip().endswith((".", "!", "?", "。", "！", "？")):
        result += "."
    if result:
        result = result[0].upper() + result[1:]
    return result


def correct_segments(segments: list[dict], enabled: bool = True) -> list[dict]:
    corrected = []
    for segment in segments:
        item = dict(segment)
        original = str(item.get("text", ""))
        item["original_text"] = item.get("original_text", original)
        item["text"] = correct_text(original, enabled=enabled)
        corrected.append(item)
    return corrected
