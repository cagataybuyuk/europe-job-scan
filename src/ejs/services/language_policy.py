"""FUNCTION-05 language and sponsorship policy (owner-approved 26 Nov 2026). Pure, offline, deterministic.

Versioned separately from the approved criteria artifact (which is not modified) and from the scoring engine
(weights and thresholds unchanged). Advertisement language and mandatory working language are separate fields.

Decision matrix (advertisement language x sponsorship status):

| advertisement            | offered   | unknown          | explicitly unavailable |
| english                  | evaluate  | evaluate         | exclude (existing K2)  |
| non_english              | watchlist | policy exclusion | exclude (existing K2)  |
| multilingual incl. English | evaluate | evaluate         | exclude (existing K2)  |
| undetermined             | review    | review           | exclude (existing K2)  |

Contradictory sponsorship statements are never an explicit refusal: English/multilingual rows evaluate (and the
Function 04 contradiction rule asks for review), every other row is a review. Language is detected from the posting
BODY only (never the title); mixed or thin evidence is never "non_english". A watchlist entry is a logical
classification, not application authority: it never enters the application queue.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

POLICY_VERSION = "EJS-LANG-SPONSOR-POLICY-1.0"
POLICY_APPROVAL = "owner instruction 2026-11-26 (Function 05 real-evidence session); criteria artifact unchanged"
WATCHLIST = "foreign_language_sponsorship_watchlist"
WATCHLIST_SCHEMA = "EJS-FOREIGN-LANGUAGE-SPONSORSHIP-WATCHLIST-1"
HISTORY_SCHEMA = "EJS-POLICY-EXCLUSION-HISTORY-1"
MIN_STOPWORD_HITS = 12
DOMINANCE = 0.85
MIN_EACH_FOR_MULTILINGUAL = 8

# Small distinct stop-word sets (words shared with English are left out on purpose).
_STOP = {
    "english": "the and of with for you your will we our have has be are that this from not to by they their can".split(),
    "dutch": ("het een van op te voor met je jij wij onze dat zijn hebt ook naar bij als aan om niet wordt worden uit "
              "jouw ons maar kan kun de en").split(),
    "german": "der das und ist nicht mit für wir sie ein eine den dem zu von auf bei auch oder werden haben".split(),
    "french": "les des une est avec vous nous dans sur que qui du aux ce cette".split(),
}
_SETS = {name: frozenset(words) for name, words in _STOP.items()}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
MATRIX: dict[tuple[str, str], tuple[str, str]] = {
    ("english", "offered"): ("evaluate", "english_advertisement"),
    ("english", "unknown"): ("evaluate", "english_advertisement"),
    ("english", "unavailable"): ("exclude_existing_k2", "explicit_no_sponsorship_existing_policy"),
    ("non_english", "offered"): ("watchlist", "non_english_advertisement_with_explicit_sponsorship"),
    ("non_english", "unknown"): ("policy_exclusion", "non_english_advertisement_sponsorship_unknown"),
    ("non_english", "unavailable"): ("exclude_existing_k2", "explicit_no_sponsorship_existing_policy"),
    ("multilingual_including_english", "offered"): ("evaluate", "multilingual_advertisement_including_english"),
    ("multilingual_including_english", "unknown"): ("evaluate", "multilingual_advertisement_including_english"),
    ("multilingual_including_english", "unavailable"): ("exclude_existing_k2", "explicit_no_sponsorship_existing_policy"),
    ("undetermined", "offered"): ("review", "advertisement_language_undetermined"),
    ("undetermined", "unknown"): ("review", "advertisement_language_undetermined"),
    ("undetermined", "unavailable"): ("exclude_existing_k2", "explicit_no_sponsorship_existing_policy"),
}


def detect_ad_language(text: str) -> dict[str, Any]:
    """Advertisement language from stop-word evidence in the posting body. Thin or mixed evidence is never
    ``non_english``: it is ``multilingual_including_english`` (both substantial) or ``undetermined``."""
    hits = {name: 0 for name in _SETS}
    for word in _WORD.findall(text.casefold()):
        for name, words in _SETS.items():
            if word in words:
                hits[name] += 1
    other = {k: v for k, v in hits.items() if k != "english"}
    other_total, english = sum(other.values()), hits["english"]
    total = english + other_total
    if total < MIN_STOPWORD_HITS:
        status = "undetermined"
    elif english >= DOMINANCE * total:
        status = "english"
    elif other_total >= DOMINANCE * total:
        status = "non_english"
    elif english >= MIN_EACH_FOR_MULTILINGUAL and other_total >= MIN_EACH_FOR_MULTILINGUAL:
        status = "multilingual_including_english"
    else:
        status = "undetermined"
    main = max(other, key=lambda k: (other[k], k)) if other_total else None
    return {"status": status, "basis": "posting_body_stopword_hits", "stopword_hits": hits,
            "dominant_non_english": main if status == "non_english" else None}


def sponsorship_status(evidence: Any) -> str:
    """offered | unavailable | contradictory | unknown (hedged, conditional and absent wording are unknown)."""
    if evidence.explicit_no_sponsorship and evidence.explicit_sponsorship_offered:
        return "contradictory"
    if evidence.explicit_no_sponsorship:
        return "unavailable"
    if evidence.explicit_sponsorship_offered:
        return "offered"
    return "unknown"


def decide(ad_status: str, sponsorship: str) -> dict[str, str]:
    if sponsorship == "contradictory":
        if ad_status in ("english", "multilingual_including_english"):
            action, reason = "evaluate", "sponsorship_contradictory_review_by_f04_contradiction_rule"
        else:
            action, reason = "review", "sponsorship_contradictory_with_non_english_or_undetermined_advertisement"
    else:
        action, reason = MATRIX[(ad_status, sponsorship)]
    return {"action": action, "reason_code": reason, "advertisement_language": ad_status, "sponsorship": sponsorship,
            "policy_version": POLICY_VERSION}


def canonical_identity(ats: str, region: str, tenant: str, posting_id: str) -> str:
    return f"{ats}:{region}:{tenant}:{posting_id}"


def _merge(store: Mapping[str, Any] | None, schema: str, records: list[Mapping[str, Any]]) -> dict[str, Any]:
    if store is not None and (not isinstance(store, Mapping) or store.get("schema") != schema
                              or not isinstance(store.get("entries"), dict)):
        raise ValueError("POLICY_STORE_SCHEMA")
    entries = {k: dict(v) for k, v in (store or {"entries": {}})["entries"].items()}
    for record in records:
        key, seen = record["identity"], record["observed_at"]
        old = entries.get(key)
        entry = {k: v for k, v in record.items() if k not in ("observed_at", "evidence_sha256", "reasons", "kind")}
        entry["first_seen"] = min(seen, old["first_seen"]) if old else seen
        entry["last_seen"] = max(seen, old["last_seen"]) if old else seen
        if schema == HISTORY_SCHEMA:
            history = list(old["history"]) if old else []
            item = {"observed_at": seen, "kind": record["kind"], "reasons": sorted(record["reasons"]),
                    "evidence_sha256": record["evidence_sha256"], "policy_version": POLICY_VERSION}
            if item not in history:                    # re-assessing the same evidence adds nothing
                history.append(item)
            entry["history"] = sorted(history, key=lambda h: (h["observed_at"], h["kind"], h["evidence_sha256"]))
        entries[key] = entry
    return {"schema": schema, "policy_version": POLICY_VERSION, "entries": dict(sorted(entries.items()))}


def merge_watchlist(store: Mapping[str, Any] | None, records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Deduplicated by canonical opportunity identity; first_seen is kept, last_seen advances."""
    return _merge(store, WATCHLIST_SCHEMA, records)


def merge_history(store: Mapping[str, Any] | None, records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Excluded listings stay in history with every distinct reason set; nothing is ever removed."""
    return _merge(store, HISTORY_SCHEMA, records)


def reporting_section(watchlist_store: Mapping[str, Any]) -> dict[str, Any]:
    """Non-empty only: an empty watchlist yields no section and no count at all."""
    entries = list(watchlist_store["entries"].values())
    if not entries:
        return {}
    return {WATCHLIST: {"count": len(entries), "application_queue_authority": False,
                        "note": "Logical classification only; never enters the application queue automatically.",
                        "opportunities": entries}}
