"""Optional name detection with Presidio (spaCy's German model), run locally.

Finds people the letter-convention rules can't see, such as a name only in running prose
("wie mit Jonas Becker besprochen"). It only adds names; the rules and patterns still run.
"""

from __future__ import annotations

import re
from functools import lru_cache

MIN_SCORE = 0.6


@lru_cache(maxsize=1)
def _analyzer():
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
    except ImportError as e:
        raise RuntimeError(
            "BRIEFKASTEN_REDACT_ENGINE=rules+presidio needs `uv sync --extra pii` and "
            "`python -m spacy download de_core_news_lg`."
        ) from e
    nlp = NlpEngineProvider(
        nlp_configuration={"nlp_engine_name": "spacy", "models": [{"lang_code": "de", "model_name": "de_core_news_lg"}]}
    ).create_engine()
    return AnalyzerEngine(nlp_engine=nlp, supported_languages=["de"])


def presidio_names(text: str) -> list[str]:
    from .redact import _clean_name  # same filter as the rules: drops organisations, streets, short words

    found = _analyzer().analyze(text, language="de", entities=["PERSON"], score_threshold=MIN_SCORE)
    names = {_clean_name(text[r.start : r.end]) for r in found}
    # spaCy tags blank form lines ("______") as people; a name has at least one letter.
    return sorted((n for n in names if n and re.search(r"[A-Za-zÄÖÜäöüß]", n)), key=len, reverse=True)
