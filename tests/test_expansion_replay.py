"""Synthetic country replay tests; never invoke a transport."""
from dataclasses import replace

import pytest

from ejs.services.expansion_replay import (
    COUNTRIES, RESEARCH, initial_checkpoint, replay_batch,
)


TENANT = "KPMGNederland"


def page(country, offset=0, total=0, ids=(), *, tenant=TENANT):
    return {
        "offset": offset, "limit": 100, "totalFound": total,
        "content": [
            {"id": jid, "name": "Synthetic analyst",
             "location": {"city": "Synthetic city", "country": country},
             "company": {"identifier": tenant}}
            for jid in ids
        ],
    }


def test_research_scope_and_reported_counts():
    cp = initial_checkpoint()
    assert len(RESEARCH) == 27
    assert len(COUNTRIES) == 9
    assert len(cp.cursors) == 243
    assert len({(c.tenant, c.country) for c in cp.cursors}) == 243
    assert sum(sum(counts) for _, _, _, counts in RESEARCH) == 4508
    assert [sum(row[3][i] for row in RESEARCH) for i in range(9)] == [
        867, 329, 1401, 325, 170, 73, 549, 567, 227,
    ]
    assert sum(1 for _, _, _, counts in RESEARCH for n in counts if n > 0) == 121
    assert not any("rituals" in c.tenant.casefold() for c in cp.cursors)


def test_resume_and_cross_country_dedup():
    cp = initial_checkpoint()
    first = replay_batch(cp, {
        (TENANT, "NL", 0): page("NL", total=2, ids=("a",)),
    }, max_pages=1)
    assert first.pages == 1
    assert first.checkpoint.cursors[0].offset == 1
    assert first.checkpoint.cursors[0].status == "pending"
    second = replay_batch(first.checkpoint, {
        (TENANT, "NL", 1): page("NL", offset=1, total=2, ids=("b",)),
        (TENANT, "IE", 0): page("IE", total=2, ids=("a", "c")),
    }, max_pages=2)
    assert [x["posting_id"] for x in second.listings] == ["b", "c"]
    assert second.duplicates == 1
    assert second.checkpoint.cursors[0].status == "complete"
    assert second.checkpoint.cursors[1].status == "complete"
    assert second.checkpoint.cursors[1].seen == ("a", "c")
    assert cp.cursors[0].offset == 0


def test_default_budgets_and_historical_zero_pairs():
    cp = initial_checkpoint()
    fixtures = {(c.tenant, c.country, 0): page(c.country, tenant=c.tenant)
                for c in cp.cursors}
    batch = replay_batch(cp, fixtures)
    assert batch.pages == 60
    for _, tenant, _, _ in RESEARCH:
        completed = [c for c in batch.checkpoint.cursors
                     if c.tenant == tenant and c.status == "complete"]
        assert len(completed) <= 5
    assert batch.checkpoint.cursors[1].status == "complete"
    assert sum(c.status == "pending" for c in batch.checkpoint.cursors) == 183


@pytest.mark.parametrize("kwargs", [
    {"max_pages": 61}, {"max_per_employer": 6},
    {"max_pages": True}, {"max_per_employer": 0},
])
def test_budget_cannot_be_loosened(kwargs):
    with pytest.raises(ValueError, match="EXPANSION_BUDGET_INVALID"):
        replay_batch(initial_checkpoint(), {}, **kwargs)


@pytest.mark.parametrize("bad", [
    page("DE", total=1, ids=("a",)),
    page("NL", offset=1, total=2, ids=("a",)),
    page("NL", total=2),
    page("NL", total=2, ids=("a", "a")),
    page("NL", total=1, ids=("a",), tenant="Other"),
    page("NL", total=1, ids=("bad/id",)),
    {"offset": True, "limit": 100, "totalFound": 0, "content": []},
])
def test_bad_page_is_atomic_and_blocks_tenant(bad):
    result = replay_batch(initial_checkpoint(), {(TENANT, "NL", 0): bad}, max_pages=1)
    cursor = result.checkpoint.cursors[0]
    assert cursor.status == "error"
    assert cursor.offset == 0
    assert not result.listings
    later = replay_batch(result.checkpoint, {
        (TENANT, "NL", 0): page("NL"),
        ("Devoteam", "NL", 0): page("NL", tenant="Devoteam"),
    }, max_pages=1)
    assert later.checkpoint.cursors[0] == cursor
    assert later.checkpoint.cursors[1].status == "pending"
    assert later.checkpoint.cursors[9].status == "complete"


@pytest.mark.parametrize("bad", [
    page("NL", offset=1, total=3, ids=("b",)),
    page("NL", offset=1, total=2, ids=("a",)),
])
def test_drift_after_resume_keeps_prior_cursor(bad):
    first = replay_batch(initial_checkpoint(), {
        (TENANT, "NL", 0): page("NL", total=2, ids=("a",)),
    }, max_pages=1)
    later = replay_batch(first.checkpoint, {
        (TENANT, "NL", 1): bad,
    }, max_pages=1)
    assert later.checkpoint.cursors[0].status == "error"
    assert later.checkpoint.cursors[0].offset == 1
    assert later.checkpoint.cursors[0].seen == ("a",)
    assert not later.listings


def test_missing_fixture_is_not_empty_or_complete():
    r = replay_batch(initial_checkpoint(), {}, max_pages=1)
    assert r.checkpoint.cursors[0].status == "error"
    assert r.pages == 1


def test_wrong_checkpoint_scope_and_cursor_rejected():
    cp = initial_checkpoint()
    for bad in (replace(cp, scope_version="other"),
                replace(cp, cursors=cp.cursors[1:]),
                replace(cp, cursors=(replace(cp.cursors[0], offset=1), *cp.cursors[1:]))):
        with pytest.raises(ValueError, match="EXPANSION_CHECKPOINT_REVIEW_REQUIRED"):
            replay_batch(bad, {})
