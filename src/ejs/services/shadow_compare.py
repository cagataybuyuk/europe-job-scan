"""Shadow comparison (ROADMAP Faz 1.7), pure: EJS decision vs a supplied Sheet decision -> diff report.

Validated ONLY with synthetic data; never run against the real Sheet CSV.
Labels are compared after normalisation; a missing counterpart is reported, never dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

_ALIASES = {"apply": "auto_apply", "auto_apply": "auto_apply", "skip": "auto_skip", "auto_skip": "auto_skip",
            "needs_user": "needs_user", "review": "needs_user"}


def norm_label(v: str | None) -> str:
    return _ALIASES.get((v or "").strip().lower().replace(" ", "_"), "unknown")


@dataclass(frozen=True)
class ShadowDiff:
    agree: tuple[str, ...]
    disagree: tuple[tuple[str, str, str], ...]   # (listing_id, ejs, sheet)
    only_ejs: tuple[str, ...]
    only_sheet: tuple[str, ...]

    @property
    def agreement_rate(self) -> float | None:
        n = len(self.agree) + len(self.disagree)
        return None if n == 0 else len(self.agree) / n


def compare_decisions(ejs: Mapping[str, str], sheet: Mapping[str, str]) -> ShadowDiff:
    agree, dis = [], []
    for k in sorted(set(ejs) & set(sheet)):
        a, b = norm_label(ejs[k]), norm_label(sheet[k])
        (agree if a == b and a != "unknown" else dis).append(k if a == b and a != "unknown" else (k, a, b))
    return ShadowDiff(tuple(agree), tuple(dis), tuple(sorted(set(ejs) - set(sheet))), tuple(sorted(set(sheet) - set(ejs))))
