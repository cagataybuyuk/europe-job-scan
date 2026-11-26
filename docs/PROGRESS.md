# Progress

## 2026-11-26 — W3.5 çevrimdışı gölge keşif entegrasyonu

Gölge keşif CLI'ı, PR #126 üzerindeki ülke kapsamlı replay ve özel
checkpoint deposunu açık bir sentetik moddan çalıştırıyor. Rapor 27 işverenin
dokuz ülkedeki kapsamını, benzersiz ilanları ve sentetik kanıtı gösteriyor.
Devam etme, aynı girdilere bağlı kalıyor ve bir rapor yazımı kesilse bile
dayanıklı kayıttan kümülatif görünümü yeniden oluşturuyor. Dört işverenli
canlı pilotun sınırları korunuyor. Kullanım, testler ve canlıya geçiş kapıları
[W3.5 çevrimdışı keşif belgesinde](W35_OFFLINE_EXPANSION.md).

### #79 checkpoint

İzole `feat/ejs-w35-discovery-integration` worktree'si, PR #126'nın doğrulanan
`36018958e2875b6ce8c603d3e60af521de154aa6` baş commit'i üzerinde. PR #126'nın
dokuz CI kontrolü başarılı. Python 3.12 ile ağ erişimi kapalı olarak 316
hedefli test ve çekirdek CI listesindeki 2.638 test geçti. Gizli bilgi taraması,
yetki sınırı kontrolü ve 40 workflow dosyasının YAML ayrıştırması geçti.
Yerel `actionlint` indirmesi `github.com` DNS çözümleme hatasıyla durdu.

Git dizininde `index.lock` yazımı `Operation not permitted` hatası verdi.
Entegrasyon değişiklikleri worktree'de duruyor. Commit, push ve PR #126 dalına
stacked draft PR bekliyor. GitHub, [#79](https://github.com/cagataybuyuk/europe-job-scan/issues/79)
yorum yazımında onay istedi. Oturumun `Approval: Never` ayarı nedeniyle kayıt
yerelde tutuldu. Canlı genişletme için ülke filtresi, sayfalama tutarlılığı,
izinli kapsam, istek limitleri ve canlı checkpoint kanıtı hâlâ doğrulanmalı.

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

## 2026-11-26 — FUNCTION-02 Listing Collection (live-feed adapter, gated)

Added a provider-shaped live page adapter on top of the W3.5 replay: documented `country` filter URL,
case-insensitive country/tenant binding, fail-closed validation, a carrier-backed `live_replay_batch`,
a durable-store live hook and an `--expansion-live` CLI limited to `LIVE_APPROVED_PAIRS` (the owner-approved
`KPMGNederland` / `NL` pilot pair). One bounded pilot ran: 3 of 5 GETs, 197 unique listings, stable first-page
recheck. Review fixes (F02-01, F02-02): request accounting and recheck evidence persist in the private bundle,
projection is row-level, and coverage is measured over the approved scope. See
[FUNCTION-02 Listing Collection](FUNCTION_02_LISTING_COLLECTION.md) for criteria status, evidence, blockers and
owner decisions.
