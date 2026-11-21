"""Deduplication key ``company + normalized role + location`` (ROADMAP Faz 1.6). Pure.

A listing missing any key part gets no key and is reported ``unkeyed`` (never silently
dropped or merged). First occurrence in input order wins, so results are deterministic.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

_LEGAL = {"gmbh", "ag", "ltd", "limited", "bv", "nv", "sa", "sas", "se", "inc", "llc", "plc", "oy", "ab", "as", "spa", "srl", "kg", "co"}
_GENDER = re.compile(r"\(\s*[mfwdxi]\s*(?:/\s*[mfwdxi]\s*){1,3}\)|\b[mfwd]\s*/\s*[mfwd]\s*/\s*[mfwdx]\b")
_NONWORD = re.compile(r"[^a-z0-9+#]+")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def normalize_company(company: str) -> str:
    toks = [t for t in _NONWORD.sub(" ", _fold(company)).split() if t]
    while len(toks) > 1 and toks[-1] in _LEGAL:
        toks.pop()
    return " ".join(toks)


def normalize_role(role: str) -> str:
    return " ".join(_NONWORD.sub(" ", _GENDER.sub(" ", _fold(role))).split())


def normalize_location(location: str) -> str:
    return " ".join(_NONWORD.sub(" ", _fold(location.split(",")[0])).split())


def dedupe_key(company: str, role: str, location: str) -> str | None:
    parts = [normalize_company(company or ""), normalize_role(role or ""), normalize_location(location or "")]
    return None if not all(parts) else "|".join(parts)


@dataclass(frozen=True)
class DedupeResult:
    kept: tuple[Mapping[str, str], ...]
    duplicates: tuple[tuple[Mapping[str, str], str], ...]   # (listing, reason)
    unkeyed: tuple[Mapping[str, str], ...]
    new_keys: tuple[str, ...]


def dedupe_listings(listings: Iterable[Mapping[str, str]], existing_keys: Iterable[str] = ()) -> DedupeResult:
    existing = frozenset(existing_keys)
    seen = set(existing)
    kept, dups, unkeyed, new = [], [], [], []
    for item in listings:
        key = dedupe_key(item.get("company", ""), item.get("role", ""), item.get("location", ""))
        if key is None:
            unkeyed.append(item)
        elif key in seen:
            dups.append((item, "duplicate_of_existing" if key in existing else "duplicate_in_batch"))
        else:
            seen.add(key)
            kept.append(item)
            new.append(key)
    return DedupeResult(tuple(kept), tuple(dups), tuple(unkeyed), tuple(new))
