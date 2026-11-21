# EJS Mimari — yerel-öncelikli omurga (TASLAK r3, AIW-14; r2 AIW-11; r1 AIW-9)

Durum: taslak. Karar kaynağı: kart 0967211d (21 Kasım 2026) K5; yön KULLANICI ONAYLI (AIW-6 yorumları `3795ccd7-f452-427c-9398-76f1a3904b5e`, `3a64b389-1f39-4e87-a9df-10fb91ca25ce`). Repo değiştirilmedi. CLAUDE.md değişikliği ayrı PR + kullanıcı merge onayı gerektirir (onay merge onayı değildir); o zamana kadar mevcut CLAUDE.md kuralı ("operasyon = Sheet", "Daily/claude.ai") geçerlidir. Yeni ücretli hizmet yok; bulut çalıştırıcı seçilmedi. Varsayım (kullanıcı kararı): Mac açık ve oturum açık.

> **Güncelleme (21 Kasım 2026):** PR/merge sürecine dair ifadeler için geçerli kural `CLAUDE.md` "Değişiklik akışı"dır (kullanıcı kararı: bağımsız inceleme ve PR başına merge onayı kaldırıldı). Mimari kararlar değişmedi.

## 1. İlkeler
1. **SQLite ledger tek doğruluk kaynağı** (başvuru durumu, olaylar, kuyruk, sağlık sinyalleri).
2. **Kurallar repo'da sürümlü YAML** (skor, Auto Apply, CV seçimi, yanıt bankası, OutcomeSource kuralları); kodda sabit yok. Kişisel değerler (yanıt bankası içeriği, watchlist) repo dışı owner-only; repo'ya yalnız şema/şablon.
3. **Tek LaunchAgent zinciri**; modüller birbirini dosya/ledger arayüzüyle çağırır, ağ taşıyıcısı enjekte edilir.
4. **Sheet = isteğe bağlı SALT-OKUNUR dışa aktarım**; Sheet'e yazma yok, Sheet'ten kural/durum okunmaz (geçişte yalnız CSV içe aktarımı, §4).
5. Fail-closed; tahmin yok; CAPTCHA/MFA atlatma yok; `runtime.json`/`STOP` yalnız kullanıcıda (PRODUCT §Değişmeyenler).

## 2. Bileşen şeması
```
 rules/*.yaml (repo, sürümlü) ──────────────┐ (tüm modüller okur, yazmaz)
 owner-only: watchlist.csv, answers.yaml     │
                                             v
 [discover] ──► ledger.opportunity(state=discovered)
 [triage]   ──► state=triaged{apply|skip|needs_user}, reason_code, score
 [manifest] ──► state=manifested ; engine-queue/inbox/<exec_id>.json (EJS-EXECUTION-MANIFEST-1)
 [engine]   ──► inbox→processing→outbox/archive ; ledger: started→submitted|failed|awaiting_user
 [outcome]  ──► OutcomeSource.fetch → apply_outcome() ──► ledger.outcome_event + application.status
 [notifier] ──► ledger'ı okur; macOS bildirimi + günlük özet dosyası
 [export]   ──► (isteğe bağlı) ledger → CSV/Sheet-içe-aktarım dosyası, tek yönlü
```
| Modül | Girdi | Çıktı / arayüz | Kim yazar |
|---|---|---|---|
| discover | watchlist (repo dışı), resmi kaynaklar, LinkedIn yalnız URL çözme | `opportunity` satırları (UNIQUE dedupe_key) | yalnız ledger API |
| triage | opportunity + `criteria.yaml` | karar etiketi + neden kodu; belirsiz → `needs_user` | ledger API |
| manifest | `apply` kararlı ilan + CV seçim kuralı + onaylı yanıt bankası | manifest dosyası `inbox/`; eksik yanıt → ilan atlanır, `profile_gap` kaydı | ledger API + inbox |
| engine | `inbox/`, `control/runtime.json` | ledger `mark_started` (tıklamadan önce commit) → sonuç | mevcut motor çekirdeği |
| outcome | OutcomeSource adaptörü | `outcome_event` (UNIQUE `canonical_event_id`) + `source_trace` (UNIQUE canonical_event_id+adapter_kind+adapter_ref; çoklu) | yalnız `apply_outcome()` |
| notifier | ledger, sağlık tablosu | bildirim, `daily_summary.md` | salt okur |

- **Tek yazar:** ledger'a yalnız tek bir Python erişim katmanı (transaction + dosya kilidi) yazar; modüller SQL'i doğrudan çalıştırmaz.
- **Kuyruk dizini ve `control/` mevcut sözleşme** (ENGINE_QUEUE) korunur; yeni modül `control/`'a asla yazmaz.

### Durum makinesi (opportunity/application)
`discovered → triaged(skip | needs_user | apply) → manifested → started → submitted → {received → interview → offer} | rejected`
Yan durumlar: `awaiting_user` (CAPTCHA/MFA/bilinmeyen şema), `failed` (gönderim belirsizse **yeniden denenmez**), `paused`. Kurallar: çatışmasız olaylar için geri düşme yok (monoton); tek açık istisna: çatışan kimlik kümesine yeni kimlik girdiği her ingest adımında (conflict zaten true olsa da) çatışan olaylar dışlanıp durum kalan kümeden yeniden hesaplanır, `conflict`+`needs_user`+`state_rollback` kaydı (PRODUCT sözleşme md.2); `submitted` yalnız teyit kanıtıyla; red terminal, tarihsel `submitted` silinmez; `offer` hiçbir otomatik eylem tetiklemez, yalnız bildirim.

### Hata ve duraklatma davranışı
| Durum | Davranış |
|---|---|
| Kural/YAML geçersiz (ağırlık toplamı ≠100, bilinmeyen anahtar) | Modül başlamaz, `health.error` yazılır, zincir o turda durur (fail-closed) |
| Kaynak/ağ hatası | O kaynak atlanır, hata sayacı artar; diğer kaynaklar sürer; üst üste N hata → o kaynak `degraded` |
| Bilinmeyen şema, CAPTCHA/MFA/login, host/CV uyuşmazlığı | O ilan `awaiting_user`; motor durur/atlar, atlatma yok |
| Belirsiz gönderim (tık sonrası yanıt yok) | `failed_uncertain`, yeniden deneme yok, `needs_user` |
| Yanlış başvuru, beklenmeyen şema, `STOP` | Rampa R1'e düşer (ROADMAP); gönderim durur. Yeniden açmak yalnız kullanıcı |
| Ledger kilitli/bozuk | Zincir durur, bildirim; otomatik onarım yok |
Duraklatma kullanıcının `STOP`/`runtime.json` kontrolüyle; sistem kendini duraklatabilir (yukarıdaki durumlar) ama **kendini yeniden açamaz**.

### LaunchAgent zinciri
Tek plist, tek giriş noktası `ejs run-chain` (öneri adı); sıra sabit: `discover → triage → manifest → engine → outcome → notifier`. Her adım bir önceki adımın `run_id` kaydını ledger'dan kontrol eder; önceki adım başarısızsa sonrakiler **gönderim yapanlar dahil** çalışmaz ama `notifier` her durumda çalışır (sessiz arıza önleme). Zamanlama: günlük 08:30 (mevcut) + `RunAtLoad`; ikinci tetik `StartInterval` ile catch-up kontrolü. Çalışma kilidi: aynı anda tek zincir.

### Mac uyku/kapalı riski ve catch-up (ayrı iş kalemi, ROADMAP 2.10)
Mevcut davranış: uyku/kapalıyken kaçan çalışma atlanır (ENGINE_QUEUE). Öneri: zincir her başlangıçta `last_successful_run` ile beklenen takvimi karşılaştırır; kaçan gün varsa **tek** telafi turu çalışır (birikmiş günleri tek tek tekrarlamaz; günlük gönderim sınırı kaçırılan günler için biriktirilmez). `pmset`/enerji ayarı değiştirmek kullanıcı kararıdır; sistem yalnız "son çalışma X saat önce" uyarısı verir. Kabul: saat enjekte edilen testte 2 gün kaçan zincir → 1 telafi turu, sınır aşılmaz; oturum açık değilken LaunchAgent çalışmadığı belgelenir (doğrulanmadı: plist'in yüklü olup olmadığı bu görevde okunmadı).

## 3. Sessiz arıza önleme
- **QUEUE_EMPTY teşhisi:** motor boş `inbox` bulursa "DONE" değil `QUEUE_EMPTY` yazar, yanında `last_discover_at`, `last_triage_at`, son turda kaç ilan keşfedildi/elendi/`needs_user`/`apply` (neden kırılımı). Boşluk nedeni sınıflanır: `feeder_dead` (keşif yok), `all_filtered` (hepsi elendi), `awaiting_user_backlog`, `paused`.
- **"Besleyici ölü" uyarısı:** `discover` son başarılı turu > `<EŞİK_SAAT, öneri 36>` ise veya ardışık sıfır-ilan turları `<N, öneri 3>` ise `health.feeder_dead=1` ve **hemen** bildirim (günlük özetten bağımsız). Watchlist/kaynak başına ayrı yaşlanma sayacı (tek kaynak ölünce görünür olsun).
- **Günlük özet** (günde ≤1, her zaman üretilir — "hiçbir şey olmadı" da bir özettir): keşfedilen/elenen/başvurulan/`needs_user`, `QUEUE_EMPTY` nedeni, kaynak sağlığı, son zincir zamanı, Mac catch-up durumu, kalibrasyon sayacı (30 başvuruya kaç kaldı), `needs_user` toplu soru listesi. İçerikte aday verisi yok (ilan kimliği/neden kodu).
- **Ölü adam anahtarı:** özet dosyası beklenen saatte yoksa bir sonraki açılışta `RunAtLoad` bildirimi "özet üretilmedi" der (kendi ölümünü ancak bir sonraki başlatmada fark eder; Mac kapalıyken uyarı **yok** — sınır, doğrulanmadı).
- Başarı tek başına çıkış kodu 0 değildir: boş + sağlıksız = hata.

## 4. claude.ai → yerel geçiş planı
Ayrı sistemler aynı olayı işlerken çift yazım olmasın diye **tek yazar kuralı:** her iş, devredilene kadar tek sistemde; EJS Sheet'e hiç yazmaz.
| İş (bugün claude.ai Daily / Sheet) | Devir fazi | Yerel modül | Geçiş boyunca |
|---|---|---|---|
| İlan keşfi (kapalı Daily görevi; elle Sheet) | Faz 1 | discover | claude.ai keşfi zaten kapalı; çift yazım yok. Elle eklenen Sheet satırları CSV ile içe aktarılabilir (idempotent dedupe_key) |
| Skor / Auto Apply kararı (Sheet formülleri) | Faz 1 (R0 gölge) | triage + rules YAML | Gölge: EJS kararı ↔ Sheet kolonu fark raporu; karar yetkisi Sheet'te kalır, R0 geçiş kapısı kullanıcı onayı |
| CV seçimi, yanıt bankası | Faz 2 | manifest | Sheet kaynak veri olarak owner-only dışa aktarılır; devirden sonra repo-dışı YAML tek kaynak |
| Başvuru | Faz 2 (rampa) | engine | Zaten yerel |
| **Gmail outcome takibi** | **3a: CSV adaptörü; 3b: Gmail/IMAP** | outcome | 3a'da claude.ai Gmail'i okur ve Outcome Event Log'a yazar; EJS yalnız `CsvOutcomeSource` ile **okur**. 3b'de kullanıcı onayı + D2 ile EJS Gmail'i doğrudan okur; claude.ai takibi kapatılır |
| Bildirim, özet | Faz 3a | notifier | claude.ai bildirimleri kapanana kadar çift bildirim olabilir; yerelde `source` etiketi |
| Sheet güncel durum görünümü | Faz 3a sonrası | export | Salt-okunur dışa aktarım; claude.ai kapanana kadar Sheet'e **yalnız claude.ai** yazar |

**Çift yazım/çatışma kuralları (3a→3b geçişi) — sözleşmenin tek kaynağı PRODUCT "claude.ai ↔ EJS sözleşmesi" md.1–4'tür, burada aynen uygulanır:** (1) **Kanonik olay kimliği:** `canonical_event_id` (= Gmail RFC Message-ID, kaynak türünden bağımsız) UNIQUE'dir; adaptör/taşıyıcı izi `source_trace` tablosundadır `(canonical_event_id, adapter_kind, adapter_ref)` ve bir olayda birden fazla olabilir. Aynı Message ID claude.ai CSV'sinden ve IMAP'ten gelirse **1 olay + 2 iz**; farklı sınıfla gelirse yeni olay açılmaz, iz eklenir, `conflict` + `needs_user`; durum kalan kümeden yeniden hesaplanır (gerekirse `state_rollback`). Şema: `outcome_event(canonical_event_id PK, application_id, event_class, occurred_at, match_confidence, conflict)`, `source_trace(canonical_event_id FK, adapter_kind, adapter_ref, seen_at)`, `state_rollback(application_id, from_state, to_state, conflict_ids, at)` (yalnız çatışma geri almalarında; denetim kaydı, durum kaynağı değil). (2) Kesme (cutover) tarihi `T`: `T`'den önce `occurred_at`'li olaylar CSV'den, sonrakiler IMAP'ten beklenir; örtüşme penceresinde (öneri 7 gün) ikisi de okunur, kanonik UNIQUE sayesinde mükerrer olay oluşmaz, ikinci taşıyıcı yalnız `source_trace` ekler. (3) **Tek çatışma politikası:** güven yüksek olsa da hiçbir sınıf otomatik kazanmaz; çatışan olay (aynı kimlik/farklı sınıf; yüksek güvenli olaylar arasında red sonrası ilerleme) duruma katılmaz, `conflict` + `needs_user`; düşük güvenli olay da duruma katılmaz. Durum, olay **kümesinin** işlevidir (`occurred_at`, eşitse `canonical_event_id` sırası) → gelme sırasından bağımsız; kabul örnekleri PRODUCT md.1 (A–C) ve md.3 (D–F). (4) Monoton durum **yalnız çatışmasız olaylar için**; tek açık istisna (Model A): bir ingest çatışan kimlik kümesine en az bir yeni `canonical_event_id` eklerse (her adımda; yalnız ilk false→true geçişi değil, conflict zaten true iken ikinci geri alma dahil; küme büyümüyorsa düşüş yok) çatışan olaylar dışlanır, durum kalan kümeden yeniden hesaplanır (önceki ara durumdan düşük olabilir), `conflict`+`needs_user` ve `state_rollback(application_id, from_state, to_state, conflict_ids, at)` kaydı yazılır; `conflict` ingest ile true→false olmaz (PRODUCT md.2). (5) 3b doğrulaması: örtüşme penceresinde iki kaynağın sınıf uyumu raporu (aynı `canonical_event_id` için iki izin sınıfı); uyum `<EŞİK, kullanıcı belirler>` altındaysa claude.ai kapatılmaz. (6) claude.ai Daily kapatma **kullanıcı eylemidir**; ajan kapatmaz. (7) Geri dönüş: claude.ai Daily yeniden açılırsa ledger yine yalnız CSV okur.
Kapatma kapıları: her devirde kullanıcı onayı; geri alınabilirlik (kuralların repo sürümü).

## 5. Sheet salt-okunur dışa aktarım biçimi
- Yön: ledger → dosya; **Sheet'e API yazımı yok** (dış yazma yasağı). Üretilen CSV'yi kullanıcı isterse Sheet'e kendisi içe aktarır/`IMPORTDATA` ile okutur.
- Dosyalar (repo dışı owner-only, 0600): `export/applications.csv`, `export/outcome_events.csv`, `export/daily_summary.csv`; `export_version` başlık satırı yorumunda; her çalışmada baştan üretilir (türetilmiş, elle düzenleme kaynak değil).
- `applications.csv` sütunları: `application_id, company, role_title, location, discovery_source, ats_family, state, score, decision, reason_code, cv_id, submitted_at, last_event_class, last_event_at, needs_user_reason, rule_version`. `outcome_events.csv`: `canonical_event_id, source_traces (adapter_kind:adapter_ref listesi), application_id, event_class, occurred_at, match_confidence, conflict`.
- Kişisel veri yok (ad, telefon, adres, CV içeriği, form yanıtı yazılmaz); `company/role/location` ilan verisidir ama dosya repo dışı kalır.
- Geri okuma: yalnızca geçiş döneminde `outcome_events` *girişi* olarak Sheet→CSV içe aktarımı (CsvOutcomeSource); ledger kuralları/durumu Sheet'ten okunmaz.

## 6. Doğrulanmadı / açık
- LaunchAgent plist'inin yüklü olup olmadığı, `runtime.json` içeriği, Mac enerji ayarı okunmadı. ENGINE_QUEUE'daki "08:30, yalnız oturum açıkken" bilgisi AIW-5'ten.
- Ledger şeması (tablo/sütun adları) mevcut `policy_submission` ledger'ına eşlenmedi; burada kavramsaldır, Faz 1'de mevcut şemadan türetilmeli.
- Eşikler (36 saat, N=3, örtüşme 7 gün, uyum eşiği) öneridir; kullanıcı belirler.
- Hiçbir test yazılmadı; yalnız belge.
