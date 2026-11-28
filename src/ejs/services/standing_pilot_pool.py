"""Select an offline pilot proposal from a single verified F05 ranking."""

from collections.abc import Sequence
from dataclasses import dataclass

from ejs.services.execution_manifest import _validate_target
from ejs.services.standing_envelope import MAX_STANDING_TARGETS, canonical_posting_identity
from ejs.services.suitability import _COUNTRY_NAMES

ERROR = "PILOT_POOL_REVIEW_REQUIRED"


@dataclass(frozen=True, repr=False)
class RankedEligibleTarget:
    rank: int
    attested_country: str
    target: dict[str, str]


@dataclass(frozen=True, repr=False)
class PilotPool:
    country: str
    targets: tuple[dict[str, str], ...]


def select_pilot_pool(candidates: Sequence[RankedEligibleTarget]) -> PilotPool | None:
    """Return the highest-ranked eligible country's first five exact targets.

    The caller supplies only eligible vacancies, binding ranks to one verified
    F05 handoff and countries to reviewed attestations for the exact targets.
    This function validates selection inputs and never reads evidence, creates
    packages or publishes authority. An empty sequence yields no proposal.
    """
    try:
        return _select(candidates)
    except (PermissionError, ValueError, TypeError):
        pass
    del candidates
    raise PermissionError(ERROR) from None


def _select(candidates: Sequence[RankedEligibleTarget]) -> PilotPool | None:
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        raise PermissionError(ERROR)
    ranked = {}
    postings = set()
    for candidate in candidates:
        if (not isinstance(candidate, RankedEligibleTarget)
                or type(candidate.rank) is not int or candidate.rank < 1 or candidate.rank in ranked
                or type(candidate.attested_country) is not str
                or candidate.attested_country not in _COUNTRY_NAMES):
            raise PermissionError(ERROR)
        target = _validate_target(candidate.target)
        posting = canonical_posting_identity(target)
        if target["ats_family"] != "smartrecruiters" or posting in postings:
            raise PermissionError(ERROR)
        postings.add(posting)
        ranked[candidate.rank] = (candidate.attested_country, dict(target))
    if not ranked:
        return None
    ordered = [ranked[rank] for rank in sorted(ranked)]
    country = ordered[0][0]
    targets = tuple(target for attested, target in ordered if attested == country)
    return PilotPool(country, targets[:MAX_STANDING_TARGETS])
