# Progress

## 2026-11-25 — EJS-W2 Discovery to Application Bridge

Phase B girdisindeki 414 benzersiz ilan özel hazırlık raporuna alındı.
164 SmartRecruiters ilanı ayrıntı kanıtı bekliyor. 250 ilan destek kapsamı
dışında. Sınırlı ayrıntı zenginleştirmesi ve mevcut manifest doğrulayıcısına
hedef bağlama sentetik verilerle çevrimdışı sınandı. Kullanım ve ilk adayın
doğrulama adımı [EJS-W2 köprü belgesinde](EJS_W2_APPLICATION_BRIDGE.md).

## 2026-11-25 — Employer Watchlist Coverage

Added an offline Markdown coverage CLI for the owner-maintained watchlist CSV.
Verified the 37 employer rows and the four Phase 1b discovery-scope references
against the local export. The current registry covers one reference for
inspection. Added synthetic cases to the core regression job and ran the
focused watchlist and ATS registry tests locally (31 passed). See
[Employer Watchlist Coverage](EMPLOYER_WATCHLIST_COVERAGE.md) for usage,
coverage semantics and integration details.

Before the local commit, added regression coverage for blank separator rows,
invalid URL ports, missing markets and unresolved LinkedIn redirect URLs.
The focused suite passed again with 34 tests. The real export produced the
same coverage counts and byte-identical reports across two CLI runs.
Secret scanning and authority boundary checks passed.
