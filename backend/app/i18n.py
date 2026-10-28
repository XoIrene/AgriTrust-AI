"""Centralised bilingual (English / Kiswahili) translation layer.

Rules for the whole project:
  * never hard-code user-facing English or Swahili text in templates,
    routers, services or JavaScript;
  * always use a translation key and resolve it through :func:`translate`.

Supported languages are loaded from ``frontend/locales/<lang>.json`` so a new
language only requires one new JSON file (plus an entry in
:data:`SUPPORTED_LANGUAGES`).
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterable

from fastapi import Request

from app.config import settings

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "en": "English",
    "sw": "Kiswahili",
}

#: cookie used to remember the farmer's language choice
LANGUAGE_COOKIE = "agritrust_lang"

#: query parameter that allows switching language without a POST
LANGUAGE_QUERY_PARAM = "lang"

#: loaded catalogs, with the file mtime they were read at
_cache: Dict[str, Dict[str, str]] = {}
_stamps: Dict[str, float] = {}
_current: ContextVar[str] = ContextVar("agritrust_lang", default=DEFAULT_LANGUAGE)


def _load_catalog(lang: str) -> Dict[str, str]:
    path: Path = settings.locales_dir / f"{lang}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    # Reload when the file changed on disk, so editing a translation shows up
    # without restarting the server (a stale catalog looks exactly like a missing key).
    if lang in _cache and _stamps.get(lang) == mtime:
        return _cache[lang]
    data: Dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    catalog = {str(k): str(v) for k, v in data.items()}
    _cache[lang] = catalog
    _stamps[lang] = mtime
    return catalog


def supported_languages() -> Dict[str, str]:
    return dict(SUPPORTED_LANGUAGES)


def normalise_language(lang: str | None) -> str:
    """Return a supported language code, falling back to English."""
    if not lang:
        return DEFAULT_LANGUAGE
    code = lang.strip().lower().replace("_", "-").split("-")[0]
    return code if code in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def set_language(lang: str | None) -> str:
    code = normalise_language(lang)
    _current.set(code)
    return code


def get_language() -> str:
    return _current.get()


def negotiate_language(request: Request | None) -> str:
    """Resolve the language for a request.

    Priority: explicit query param > saved cookie > ``Accept-Language`` > default.
    """
    if request is None:
        return DEFAULT_LANGUAGE

    param = request.query_params.get(LANGUAGE_QUERY_PARAM)
    if param:
        return set_language(normalise_language(param))

    cookie = request.cookies.get(LANGUAGE_COOKIE)
    if cookie:
        return set_language(normalise_language(cookie))

    header = request.headers.get("accept-language", "")
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()
        if code.startswith("sw"):
            return set_language("sw")
        if code.startswith("en"):
            return set_language("en")
    return set_language(DEFAULT_LANGUAGE)


def translate(key: str, lang: str | None = None, /, **params: Any) -> str:
    """Resolve ``key`` to a translated string.

    Falls back to English and finally to the key itself, so a missing
    translation can never break a page.
    """
    code = normalise_language(lang or get_language())
    value = _load_catalog(code).get(key)
    if value is None:
        value = _load_catalog(DEFAULT_LANGUAGE).get(key)
    if value is None:
        value = key
    if params:
        try:
            return value.format(**params)
        except (KeyError, IndexError, ValueError):
            return value
    return value


#: short alias used inside services and routers
t = translate


def crop_label(name: str, lang: str | None = None) -> str:
    """A crop name in ``lang``; a crop with no catalog entry keeps its own name."""
    key = f"crop.{str(name or '').strip().lower()}"
    label = translate(key, lang)
    return name if label == key else label


def translator(lang: str | None = None):
    """Return a single-argument ``t(key)`` callable bound to ``lang``."""
    code = normalise_language(lang or get_language())

    def _t(key: str, /, **params: Any) -> str:
        return translate(key, code, **params)

    return _t


def translate_many(keys: Iterable[str], lang: str | None = None) -> Dict[str, str]:
    code = normalise_language(lang or get_language())
    return {key: translate(key, code) for key in keys}


#: words that give away a Kiswahili question
SWAHILI_MARKERS = (
    "habari", "shamba", "mkopo", "bei", "soko", "hewa", "bima", "fedha",
    "nataka", "nina", "wastani", "nzuri", "msaada", "kijiji", "mahindi",
    "maji", "kupanda", "kuua", "rufa", "jumla", "siku", "asubuhi",
    "mvua", "joto", "uvunaji", "mazao", "mkulima", "kilimo", "sasa",
    "kesho", "wiki", "mwezi", "mwaka", "nini", "wapi", "kwa nini", "niangalie",
)

#: words that give away an English question
ENGLISH_MARKERS = (
    "what", "when", "where", "which", "how", "why", "should", "can i", "my",
    "price", "weather", "loan", "farm", "rain", "harvest", "next", "tell",
)


def detect_content_language(text: str, default: str = DEFAULT_LANGUAGE) -> str:
    """Which language the farmer is writing in.

    Answers in kind, but a question with no clear signal (``fallback``) is assumed
    to be in the language the farmer already chose for the app, not always English:
    a Kiswahili farmer who types a short question still wants a Kiswahili answer.
    """
    lowered = text.lower()
    swahili = sum(1 for marker in SWAHILI_MARKERS if marker in lowered)
    english = sum(1 for marker in ENGLISH_MARKERS if marker in lowered)
    if swahili > english:
        return "sw"
    if english > swahili:
        return "en"
    return normalise_language(default)
