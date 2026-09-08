"""Deterministic lexical matching for already scoped capability catalogs."""
from __future__ import annotations

import re
from collections.abc import Iterable


def _terms(text: str) -> set[str]:
    terms = set()
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:+/-]*|[\u3400-\u9fff]+", text.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", token) and len(token) > 1:
            terms.update(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.add(token)
    return terms


def rank_discovery(query: str, *, name: str, aliases: Iterable[str] = (),
                   keywords: Iterable[str] = (), description: str = "") -> tuple[int, int, int] | None:
    """Higher scores sort first; callers break ties with unchanged names/IDs.

    Identifiers remain whole lexical anchors. Chinese phrases share adjacent
    character pairs, so a natural sentence need not occur verbatim in a title.
    This score selects from an authorized catalog and never grants execution.
    """
    query = query.casefold().strip()
    if not query:
        return None
    names = tuple(value.casefold().strip() for value in (name, *aliases) if value.strip())
    if query in names:
        return 3, len(query), 0
    query_terms = _terms(query)
    if not query_terms:
        return None
    keyword_text = " ".join((*names, *keywords)).casefold()
    keyword_hits = sum(term in keyword_text for term in query_terms)
    description_hits = sum(term in description.casefold() for term in query_terms)
    if keyword_hits:
        return 2, keyword_hits, description_hits
    if description_hits:
        return 1, description_hits, 0
    return None
