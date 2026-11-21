> **Güncelleme (21 Kasım 2026):** Bu belge bir **plan**dır; `git mv` ile arşive taşıma ayrı bir PR'da yapılır (bu PR yalnız planı ekler). Kullanıcı kararıyla (Paperclip görevi AIW-6, kart `5c2ade95`) "bağımsız inceleme" ve "PR başına kullanıcı merge onayı" koşulları kaldırıldı ve `CLAUDE.md` güncellendi; §2, §3 madde 8, §5 ve §6'daki "mevcut kural geçerli", "tam akış", "merge onayı gerekir" ifadeleri tarihsel taslak metnidir, geçerli kural `CLAUDE.md`'dir. §5'teki Sheet "Submit Boundary" metni Sheet'te kullanıcı tarafından elle güncellenir (ajan Sheet'e yazamaz).

# EJS Belge Arşiv Planı (TASLAK r4, Faz 0 revizyon 3 / AIW-11)

r4 (AIW-11): §6 F3 — Faz 0 PR'ına yalnız `STATUS_TEMPLATE.md` girer; ilk resmî `STATUS.md` ROADMAP Faz 0.5.1'de (CLAUDE.md PR'ı merge sonrası); D8/D9 kullanıcı onaylı (ROADMAP bağımlılık tablosu), uygulama ayrı PR + merge onayı. r3 (AIW-9) değişenleri: §2 tablosu K5/K6'ya göre (Sheet = salt-okunur dışa aktarım, Sheet'e yazma yok; Drive 02_Guncel_Durum ve Issue #79 yerine tek `STATUS.md`); §3 madde 8 (Değişiklik akışı) için risk kademeli ÖNERİ notu; §4 Sheet sekmeleri "arşiv" yönü K5 ile uyumlu; §6'ya `ARCHITECTURE.md`/`STATUS_TEMPLATE.md` eklemesi. §1 sınıflama/envanter DEĞİŞMEDİ. Tüm değişen kurallar ÖNERİ'dir; CLAUDE.md onaya kadar geçerli.

r2 (AIW-8) eklemeleri: §2'de Sheet'in iki rolü (kural kaynağı / operasyon görünümü), §3 madde 4/9 notları, **§4 Sheet sözleşme sekmeleri ↔ EJS docs**, **§5 Submit Boundary güncelleme ÖNERİSİ**; eski §4 → §6. Belge sınıflaması (§1) ve envanter kanıtı (`docs_inventory_evidence.txt`, AIW-7'den değişmeden kopyalandı) r1 ile aynıdır.

Yöntem: `~/europe-job-scan/docs/` (55 dosya, `main@0180adc`) için başlık/durum satırı okundu; `src/`, `tests/`, `CLAUDE.md`, `README.md`, `.github/` içinde dosya adı referansı arandı. **Belgelerin tam metni tek tek okunmadı** (ENGINE_QUEUE, PRODUCTION_MULTI_ATS_LAUNCH_PLAN, MVP_INDEPENDENT_OPERATION başlangıçları hariç); sınıflar başlık + referans + tarih çıkarımıdır, uygulama öncesi sahip gözden geçirmesi gerekir. Repoda hiçbir şey değiştirilmedi.

## Sınıflar
- **Aktif**: `docs/` kökünde kalır; CLAUDE.md/kod tarafından kullanılıyor veya mevcut davranışı anlatıyor.
- **Referans**: kalır ama `docs/reference/` altına (ya da kökte, "Referans" etiketli indekste); sözleşme/politika değeri var, günlük okunmaz.
- **Arşive taşı**: `docs/archive/` altına `git mv` (geçmiş korunur), başına "ARŞİV — tarihsel, güncel değil" bandı; silinmez ("çelişkiyi silme", CLAUDE.md).

## 1. Belge tablosu

Sütun "Ref": dosya adının geçtiği kod/CI/ana belge (taşımada güncellenmesi gerekir).

### Aktif (6)
| Belge | Gerekçe | Ref |
|---|---|---|
| ENGINE_QUEUE.md | GD-006 motor/kuyruk sözleşmesi; kodda atıf | `engine_queue.py`, `policy_engine/__main__.py`, CLAUDE.md |
| EXECUTION_MANIFEST.md | Manifest sözleşmesi (Faz 2 üretici bunu hedefler) | `execution_manifest.py`, CLAUDE.md |
| POLICY_SUBMISSION.md | Tek gönderim/ledger yetkisi; yeniden kullanılacak çekirdek | CLAUDE.md |
| SMARTRECRUITERS_POLICY_BROWSER.md | Tek canlı destekli ATS adaptörü | CLAUDE.md |
| MVP_INTEGRATED_SINGLE_APPLICATION.md | README'nin "güncel lane" atıfı | README |
| MVP_NATIVE_PROFILE_CORRECTION.md | README atıfı; profil düzeltme sözleşmesi | README |

### Referans (13)
| Belge | Gerekçe | Ref |
|---|---|---|
| SUBMIT1_CONDITIONAL_AUTO_SUBMIT_CONTRACT.md | CLAUDE.md "canlı gönderim yalnız SUBMIT-1 içinde": kural kaynağı | — |
| MVP_SPONSORSHIP_UNCERTAINTY_POLICY.md | Sponsorluk tahmin edilmez kuralının gerekçesi | — |
| MVP_WORK_RIGHTS_METADATA_GATES.md | Çalışma izni kapı mantığı | — |
| L0_COMMON_ATS_ADAPTER_CONTRACT.md | Faz 2 adaptör sözleşmesine girdi | — |
| MVP_DURABLE_SINGLE_SUBMISSION.md | Ledger tasarım gerekçesi (POLICY_SUBMISSION'ın öncülü) | — |
| MVP_RETAINED_SESSION.md | Oturum çiti tasarımı | — |
| MVP_CENTRAL_OUTCOME_SYNC.md | Faz 3 tracker entegrasyonunun girdisi (şu an yalnız sentetik) | — |
| MVP_INDEPENDENT_OPERATION.md | "Codex kapalıyken çalış" mimari kararı (2026-11-13) | — |
| MVP_REVIEWED_EXECUTION_SCOPE.md | İncelenmiş kapsam modeli; kapı mantığı örneği | — |
| smartrecruiters-e2e.md | SR hazırlık runtime'ı (SR adaptörü için); PRODUCTION plan "pending" diyor | — |
| FILE1_DOCUMENT_UPLOAD_RUNTIME.md | CV yükleme doğrulama sözleşmesi (Faz 2) | — |
| TMH1_TEMPLATE_MAPPING_EXECUTION_HISTORY.md | Şablon/eşleme geçmişi temeli | — |
| GD004_LINKEDIN_SOURCE_RESOLVER.md | Salt okunur URL çözücü; Faz 2 sınıflandırıcıya girdi | — |

### Arşive taşı — referanssız, güvenli (26)
| Belge | Gerekçe |
|---|---|
| BE1_BROWSER_WORKER_BOOTSTRAP.md, BE2_SAFE_FIELD_WRITER.md | Eski BE-x iş paketi notları; işlev kodda |
| GD004_ADP_LIVE_INSPECTION.md, GD004_ADP_NAVIGATION_CANARY.md, GD004_ADP_VERIFIED_SESSION_BOOTSTRAP.md | ADP canary zinciri; ADP Faz 4/P2-1 öncesi güncel değil |
| GD004_DAY_CLOSE_2026-09-15.md | Tarihli gün kapanış raporu |
| GD004_LIVE_INSPECTOR_V2.md, GD004_SHADOW_ADAPTER.md, GD004_SHADOW_FIXTURE_WRITER.md, GD004_WAVE2_TEST_QUEUE.md | GD-004 Cloud Run/Shadow DOM hattı; hedef hat GD-006 |
| GD004_ZERO_COST_USER_TRUST_BOOTSTRAP.md | GD-004 bootstrap, Apps Script hattı |
| GD005_TEST_RESOURCE_ROUTER.md, GD005_TRUST_ISOLATION_GATE.md | GD-005 tasarımları; "no executor authority", GD-006 ile geçildi |
| MVP_MAC_VALIDATION_JOB.md | Mac doğrulama işi temeli; çalışan bilgi ENGINE_QUEUE'da |
| MVP_NEXT_TARGET_DEVOTEAM_2026-11-12.md, MVP_REVIEWED_NEXT_SCOPE.md | Tek hedefe/tarihe bağlı kapsam kayıtları |
| MVP_SINGLE_APPLICATION_GAP_AUDIT_2026-11-04.md | Tarihli denetim (yerine AIW-5 denetimi) |
| MW0_MW1_IMPLEMENTATION_NOTES.md, MW2_DECISION_SHADOW.md, MW3_APPEND_ONLY_CANARY.md, MW4A_DERIVED_STATE_SHADOW.md, MW4B_DERIVED_STATE_WRITE_CANARY.md, MW4C_DERIVED_AUTHORITY_AND_UAC_SHADOW.md, MW5A_GUARDED_MUTATION_PREFLIGHT.md, MW5B_EXECUTOR_READINESS.md | MW-x "kapı/canary" katmanları (Ağustos–Eylül 2026); karmaşıklığın ana kaynağı, tarihsel |
| TEST_BOOTSTRAP_ONE_SHOT.md | GD-003 TEST sağlama runbook'u |

### Arşive taşı — koşullu: CI/test referansı var (9) — taşıma aynı PR'da referansı da güncellemeli
| Belge | Gerekçe | Güncellenecek |
|---|---|---|
| GD004_ADP_CONTINUE_CANARY.md | ADP canary | `.github/workflows/gd004-adp-continue-canary-regression.yml` |
| GD004_ADP_CONTINUE_DIAGNOSTICS.md | ADP | `gd004-adp-continue-diagnostic-regression.yml` |
| GD004_ADP_COOKIE_PREFERENCES_CANARY.md | ADP | `gd004-adp-cookie-preferences-regression.yml` |
| GD004_ADP_PHONE_CONTRACT_CANARY.md | ADP | `gd004-adp-phone-contract-regression.yml` |
| GD004_ADP_PREFERENCE_SAFE_FILL_CANARY.md | ADP | `gd004-adp-preference-safe-fill-regression.yml` |
| GD004_ADP_SAFE_FILL_CANARY.md | ADP | `gd004-adp-safe-fill-canary-regression.yml` |
| GD004_ADP_VALIDATION_CONTRACT.md | ADP | `gd004-adp-validation-contract-regression.yml` |
| GD004_ADP_PROFILE_POLICY.md | ADP profil politikası v2 | 3 workflow (`gd004-adp-profile-*-regression.yml`) |
| GD004_ZERO_COST_APPS_SCRIPT_BOOTSTRAP.md ("superseded entrypoint") | Başlığı kendi "superseded" diyor | `tests/node/gd004_gateway.test.cjs` |

Toplam: 6 aktif + 13 referans + 26 güvenli arşiv + 9 koşullu arşiv + 1 (PRODUCTION plan, aşağıda) = 55; betikle doğrulandı (eksik 0, çift 0).

### Ayrı sınıf: PRODUCTION_MULTI_ATS_LAUNCH_PLAN.md
**Referans (yerine geçilecek).** v2.0 (2026-09-18) "6 ATS lansmanı" hedefi roadmap ile çelişir (Greenhouse/Lever önce, Workday/LinkedIn son). Faz 0 sonrası ROADMAP.md'ye işaret eden "SUPERSEDED" bandı eklenir, dosya silinmez; README atıfı güncellenir.

## 2. Tek doğruluk kaynağı önerisi (4 kaynak → 1)

| Kaynak | Bugünkü rol | Öneri |
|---|---|---|
| **Repo (`main`)** | Kod + sözleşmeler | **Tasarım/kural/roadmap için tek doğruluk kaynağı**: PRODUCT.md, ROADMAP.md, INVARIANTS.md, docs/ |
| **Yerel SQLite ledger** | Başvuru durumu | **Operasyonel durumun tek doğruluk kaynağı** (hangi ilan, hangi durum, kanıt) |
| Google Sheets (EU Job Tracker) | Operasyon görünümü + kural kaynağı | **İsteğe bağlı SALT-OKUNUR dışa aktarım** (ledger → CSV, ARCHITECTURE §5); **Sheet'e yazma yok**; kurallar repo YAML'ına taşınır; claude.ai kapanana kadar claude.ai'nin yazdığı Outcome Log girdi (CSV) |
| Drive (00/01/02) | Yönetişim | Repo `docs/`'a taşınanlar dışında **salt arşiv**; 02_Guncel_Durum yerine repo'daki tek `STATUS.md` (`STATUS_TEMPLATE.md`) |
| Issue #79 | Checkpoint | `STATUS.md`'ye taşınır (2 hafta yan yana), sonra "STATUS.md'ye taşındı" notuyla kapatılır; durum kaynağı değil |

**r2 notu (Sheet iki rol taşıyor):** Sheet hem *kural kaynağı* (OPPORTUNITY-1.0, AUTOREVIEW-1.0, CV seçim kuralı, yanıt bankası, Outcome Tracking kuralları) hem *operasyon kaydı* (Applications, Outcome Event Log). Öneri aşağıdaki gibi ayrışır: kurallar koda taşınınca repo tek kural kaynağı; operasyonel durumda ledger tek kaynak; **geçiş döneminde** claude.ai'nin yazdığı Outcome Log, `OutcomeSource` (CSV/Sheet içe aktarımı) ile ledger'a **girdi** olur ve EJS Sheet'e yazmaz (PRODUCT "Tek doğruluk kaynağı", ROADMAP Faz 3a). Bu da aşağıdaki ÖNERİ'nin parçasıdır, onaya kadar CLAUDE.md geçerli.

**Mevcut kural (CLAUDE.md "Kaynak önceliği", değişmedi):** Kod = repo `main`; operasyon = EU Job Tracker Sheet; yönetişim = Drive 00/01/02 + onaylı GD dokümanları. Çelişkide: en son işveren kaydı veya yerel ledger + canlı tracker okuması > Issue #79 checkpoint > `main` dokümanları > tarihsel raporlar; çelişki silinmez, tarihle açıklanır.

**ÖNERİ — yön KULLANICI ONAYLI (yorum `3795ccd7…`, kart 0967211d); uygulama ayrı PR + kullanıcı merge onayı; o zamana kadar mevcut kural geçerli (mevcut kuralı DEĞİŞTİRİR):** yukarıdaki tablo — Sheets'i operasyon kaynağı olmaktan çıkarıp ledger'dan türetilmiş görünüme, Drive'ı salt arşive, Issue #79'u yalnız devir notuna indirir ve tasarım/kural için repoyu tek kaynak yapar. Onaylanana kadar CLAUDE.md kuralı geçerlidir; uygulanırsa CLAUDE.md ayrı PR'da güncellenir. Karar kullanıcıya aittir; Drive/Sheets içeriği bu ortamdan okunmadı (erişim yok), bu yüzden bu satırlar yapı önerisidir.

## 3. "Değişmez kurallar" 1 sayfa özeti (taslak `INVARIANTS.md`)

Kanıt: sınıflandırmanın dayandığı sha256/başlık/durum/dosya:satır envanteri `docs_inventory_evidence.txt` (repo `main@0180adc8693b`, 55 bölüm). Atıf taraması kapsamı: src, tests, CLAUDE.md, README.md, .github (docs/ içi çapraz atıflar taranmadı).

Kaynak: CLAUDE.md "Güvenlik sınırları", "Durum dili", "Repo PUBLIC", "Değişiklik akışı". Anlam değiştirilmedi, yalnız toplandı.

1. **Fail-closed.** Form şeması, host, seçenekler, CV dosyası veya başarı yanıtı beklenenden farklıysa dur. Belirsiz/çöken gönderim asla yeniden denenmez.
2. **Tahmin yok.** Çalışma izni, sponsorluk, maaş, iş geçmişi, demografik, hukuki yanıt; bilinmeyen bilinmeyen kalır. Yanıt yalnız kullanıcı onaylı profil yanıt bankasından gelir; yoksa ilan atlanır, eksik toplu sorulur. Pazarlama/talent pool izni varsayılan Hayır/boş.
3. **Atlatma yok.** CAPTCHA, MFA, login, doğrulama atlatılmaz; motor Awaiting User'da bekler. Şifre girme ve hesap açma kullanıcıya aittir.
4. **Tek gönderim.** Başlamış gönderim tekrarlanmaz; canlı gönderim yalnız SUBMIT-1 içinde (koşullu otomatik, K1), ledger `mark_started` tıklamadan önce commit. *(r2: K1 koşullu otomatik gönderimi SUBMIT-1 zaten kapsar; Sheet'teki "USER-required" kuralı için §5 önerisine bak.)*
5. **Kontrol kullanıcıda.** `engine-queue/control/` (`runtime.json`, `STOP`) yalnız kullanıcı yazar; açma/kapama kullanıcının işi. Ajanlar dokunmaz.
6. **Durum dili.** Merged ≠ canlı çalıştı. Tests PASS ≠ başvuru yapıldı. Form dolduruldu/CV yüklendi ≠ gönderildi. Submit tıklandı ≠ Applied. Applied yalnız doğru işveren+rol için teyit kanıtıyla; sonradan Rejected gelse tarihsel Applied kalır.
7. **Repo public.** CV, aday manifesti, form verisi, kişisel bilgi, secret/token/cookie commit/issue/PR yorumuna girmez; özel veri repo dışı owner-only klasörlerde; checkpoint'e yalnız durum kodu/execution_id/teknik bulgu.
8. **Değişiklik akışı.** `main`'e doğrudan push yok: branch→PR→exact-head CI→bağımsız inceleme→düzeltme→kullanıcının açık "merge" onayı→squash (head SHA sabit)→main CI. **(r3 öneri; yön KULLANICI ONAYLI (AIW-6 yorumları `3795ccd7…`, `3a64b389…`), uygulama ayrı PR + kullanıcı merge onayı gerektirir; mevcut kuralı değiştirir: gönderim/güvenlik/ledger/runtime.json yolları — `STATUS_TEMPLATE.md` §B yüksek-risk tablosu — bu tam akışta kalır; gönderimi etkilemeyen düşük risk yollarda CI yeşil + Codex QA onayıyla merge onayı haftalık toplu olabilir. Onaya kadar yukarıdaki kural aynen geçerlidir.)** Dış sistem yazımı, dış iletişim, yeni OAuth izni, ücretli kullanım için açık onay. Yeni test dosyası `ci.yml`'ye eklenir.
9. **AI sınırı.** Önce kural tabanlı; AI yalnız abonelik kotası içinde ve yalnız sıralama için, karar vermez; ücretli API yok.
10. **Dürüst rapor.** Plan ile gerçekleşeni ayır; doğrulanmayanı "doğrulanmadı" yaz; çelişkiyi silme, tarihle açıkla.

## 4. Sheet sözleşme/denetim sekmeleri ↔ EJS docs (çakışma ve sadeleşme)

Gözlem (tracker_findings §3.6): Sheet'te ~25 sözleşme/denetim sekmesi var (örn. Service Schema, Source Adapter Contract, Workday Inspector, State Transition Matrix, Auto Submit Policy Registry, Rule Registry) ve EJS repo belgeleriyle çakışan **ikinci bir mühendislik katmanı** oluşturuyor. **Sınır:** yalnız bulgular belgesinde adı geçen sekmeler eşlenebildi; ~25 sekmenin tam listesi ve içerikleri bu görevde yeniden okunmadı (xlsx yalnız Paperclip eki) → aşağıdaki eşleme **öneri**, tam sekme envanteri uygulamadan önce yapılmalı.

| Sheet sekmesi (adı bulgularda geçen) | EJS'te çakışan/ilgili | Öneri |
|---|---|---|
| State Transition Matrix | ledger durum makinesi; `POLICY_SUBMISSION.md`, "Durum dili" (INVARIANTS) | Matris koda (ledger durumları + `apply_outcome`) ve INVARIANTS'a taşınır; Sheet sekmesi salt-okunur arşiv. Çelişki varsa **tarihle** kayda geçer, silinmez |
| Auto Submit Policy Registry | `SUBMIT1_CONDITIONAL_AUTO_SUBMIT_CONTRACT.md`, `submit_policy` | Repo tek kaynak (SUBMIT-1); Sheet kaydı §5 önerisi onaylanana kadar geçerli referans |
| Rule Registry | OPPORTUNITY/AUTOREVIEW/Outcome kuralları (ROADMAP 1.4, 1.5, 3a.3) | Kurallar `criteria.yaml` + kural modüllerine; Sheet'te yalnız "bu kural şu repo sürümünde" işaretçisi |
| Source Adapter Contract | `L0_COMMON_ATS_ADAPTER_CONTRACT.md`, `ats_adapter_registry.py` | EJS belgesi tek kaynak; Sheet sekmesi arşiv. K4 (kaynak politikası) ürün belgesine işlenir |
| Workday Inspector | Faz 2.6 Workday adaptörü (henüz yok) | Faz 2'ye **girdi** olarak korunur (alan/akış bilgisi); adaptör yazıldıktan sonra arşiv |
| Service Schema | `EXECUTION_MANIFEST.md`, `engine_queue` şemaları | Çakışma → repo şeması tek kaynak; farklı alanlar manifest üreticisine (Faz 2.3) girdi |
| Outcome Tracking kuralları / Outcome Event Log | Faz 3a OutcomeSource | Kurallar koda; log **canlı girdi** olarak kalır (geçiş dönemi) |
| Candidate Profile, CV Base Library, Answer Library, User Fact Registry, Employer Watchlist | doğrudan eşdeğer yok | **Veri/yapılandırma**, mühendislik sözleşmesi değil: Sheet'te kalır veya owner-only dışa aktarılır; repo'ya yalnız şema/şablon girer (kişisel veri yok) |

Sadeleşme ilkesi: bir kavram için **tek sözleşme**. Kod/mühendislik sözleşmeleri repo'da (testle birlikte), kullanıcı verisi ve görünüm Sheet'te. Sheet'teki sözleşme sekmeleri silinmez; taşındıktan sonra "ARŞİV — bkz. repo" bandı alır. Çakışmada öncelik: repo `main` (CLAUDE.md kaynak önceliğiyle çelişmesin diye bu da §2 önerisinin parçasıdır; onaya kadar mevcut kural geçerli). Sheet'i kullanıcı düzenlediği için ajanlar Sheet'e yazmaz.

## 5. Submit Boundary kuralı güncelleme ÖNERİSİ (D8: KULLANICI ONAYLI — yorum `3a64b389-1f39-4e87-a9df-10fb91ca25ce`, 07:24Z; uygulama ayrı PR/kullanıcı eylemi + merge onayı; o zamana kadar Sheet kuralı geçerli; onay merge onayı değildir)

Çelişki: Sheet "Submit Boundary Rule: nihai Submit **USER-required**" der; EJS SUBMIT-1 koşullu otomatik gönderime izin verir; kullanıcı kararı K1: **koşullu otomatik**. Önerilen yeni metin (Sheet ve repo için, onaydan sonra):

> Nihai Submit, `submit_mode = conditional_auto` iken sistem tarafından yapılabilir; yalnız şu koşulların **tümü** sağlanırsa: (1) kill switch (`STOP`/`runtime.json`) kapalı değil ve kullanıcı tarafından açılmış; (2) ledger'da bu ilan için gönderim başlamamış (`mark_started` tıklamadan önce commit; tek gönderim); (3) form şeması, host, seçenekler ve CV beklenenle aynı (bilinmeyen şema → dur); (4) CAPTCHA/MFA/login/doğrulama yok (varsa dur, atlatma yok); (5) tüm yanıtların kaynağı onaylı yanıt bankasında (çalışma izni/sponsorluk/maaş/hukuki tahmin yok; eksik → ilan atlanır); (6) ilan `auto_apply` kararını sert engel olmadan geçti; (7) kademeli rampa (ROADMAP R0–R3) o kademeye kullanıcı tarafından açılmış ve günlük sınır aşılmamış. Aksi hâlde Submit **USER-required** kalır (`needs_user`).

Uygulama: (a) Sheet'te "Submit Boundary Rule" ve "Auto Submit Policy Registry" metinleri yukarıdakiyle değiştirilir (kullanıcı veya kullanıcı onaylı iş; ajan Sheet'e yazmaz); (b) CLAUDE.md/INVARIANTS ayrı PR'da güncellenir; (c) onaya kadar Sheet kuralı ve rampa R0/R1 (Submit öncesi durma) geçerli kabul edilir. Bu öneri çalışma izni, sponsorluk, maaş veya hukuki yanıt tahmin etme yetkisi **vermez**.

## 6. Taşıma uygulaması (ayrı onaylı PR görevi için not)
- r3: `ARCHITECTURE.md` ve `STATUS_TEMPLATE.md` (şablon; resmî kaynak değil) aynı Faz 0 PR'ında `docs/` altında yer alır. **İlk resmî `STATUS.md` Faz 0'da oluşturulmaz**; ROADMAP Faz 0.5.1'de, CLAUDE.md'nin K5/K6'ya göre güncellendiği **ayrı, yüksek riskli, tam akışlı PR** (D9 uygulaması; kullanıcı onayı verilmiştir, merge onayı ayrıca gerekir) merge olduktan sonra oluşturulur.
- Tek PR: `git mv` (geçmiş korunur), arşiv bandı, `docs/README.md` indeksi (aktif/referans/arşiv), README ve CLAUDE.md atıflarının güncellenmesi, INVARIANTS.md/PRODUCT.md/ROADMAP.md eklenmesi.
- Koşullu 9 belge: ya aynı PR'da workflow `paths:` ve node testi güncellenir, ya bu belgeler ADP kararına (P2-1) kadar yerinde kalır. **Öneri:** ilk PR'da kalsın, ikinci PR'da ADP workflow'larıyla birlikte arşivlensin (CI path filtresi sessizce işlevsizleşmesin).
- Kabul: taşıma sonrası `grep -rn` ile kırık atıf 0; exact-head CI yeşil; kod diff'i 0.
