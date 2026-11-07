from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Callable

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_RU_RE = re.compile(r"^[а-яё]+$")
_EN_RE = re.compile(r"^[a-z]+$")


def surface_tokens(text: str) -> list[str]:
    if not isinstance(text, str):
        raise ValueError("LEXICAL_TEXT_INVALID")
    normalized = unicodedata.normalize("NFKC", text.casefold())
    return _WORD_RE.findall(normalized)


def analyze_text(text: str, *, normalize_token: Callable[[str], str]) -> str:
    out = []
    for token in surface_tokens(text):
        normalized = normalize_token(token)
        if not isinstance(normalized, str) or not normalized:
            raise ValueError("LEXICAL_TOKEN_INVALID")
        out.append(normalized)
    return " ".join(out)


def fts_or_query(tokens: list[str]) -> str:
    if not tokens:
        raise ValueError("LEXICAL_QUERY_EMPTY")
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def make_normalizer(mode: str) -> Callable[[str], str]:
    if mode == "surface":
        return lambda token: token

    if mode == "snowball":
        try:
            import snowballstemmer
        except ImportError as exc:
            raise RuntimeError("SNOWBALLSTEMMER_REQUIRED") from exc
        ru = snowballstemmer.stemmer("russian")
        en = snowballstemmer.stemmer("english")

        @functools.lru_cache(maxsize=500_000)
        def normalize(token: str) -> str:
            if _RU_RE.fullmatch(token):
                return ru.stemWord(token)
            if _EN_RE.fullmatch(token):
                return en.stemWord(token)
            return token

        return normalize

    if mode == "lemma":
        try:
            import pymorphy3
            import snowballstemmer
        except ImportError as exc:
            raise RuntimeError("PYMORPHY3_AND_SNOWBALLSTEMMER_REQUIRED") from exc
        morph = pymorphy3.MorphAnalyzer()
        en = snowballstemmer.stemmer("english")

        @functools.lru_cache(maxsize=500_000)
        def normalize(token: str) -> str:
            if _RU_RE.fullmatch(token):
                return morph.parse(token)[0].normal_form
            if _EN_RE.fullmatch(token):
                return en.stemWord(token)
            return token

        return normalize

    raise ValueError("LEXICAL_ANALYZER_INVALID")
