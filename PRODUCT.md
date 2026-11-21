> **Güncelleme (21 Kasım 2026):** Kullanıcı kararıyla (Paperclip görevi AIW-6, kart `5c2ade95` "Her yolda doğrudan ben") "bağımsız inceleme" ve "PR başına kullanıcı merge onayı" koşulları kaldırıldı; CI yeşilse ajan tüm yollarda birleştirebilir (`CLAUDE.md` "Değişiklik akışı"). Bu belgede (özellikle K6, "risk kademeli akış", "haftalık toplu onay", "tam akış" ifadeleri) aksini söyleyen yerler tarihsel taslak metnidir; geçerli kural `CLAUDE.md`'dir. `runtime.json`/`STOP` yalnız kullanıcıda kalır ve güvenlik sınırları aynen geçerlidir.

# EJS — Ürün Tanımı (TASLAK r6, Faz 0 revizyon 5 / AIW-17)

Durum: taslak. K1/K5/K6 yön kararları kullanıcı onaylıdır (kayıtlar K tablosunda); CLAUDE.md/Sheet uygulaması ve diğer açık kalemler onay bekler. Kaynak: AIW-6 analizi + `tracker_findings.md` + kullanıcı kararları (AIW-6 §9, §11), AIW-5 denetimi, EJS repo `main@0180adc` (salt okuma). r1'den (AIW-7) farkı: tracker verisi, 4 bağlayıcı karar, OutcomeSource portu. **r4 (AIW-11, QA AIW-10 F1/F2/F3):** kanonik olay kimliği + `source_trace` (F1), tek çatışma politikası (F2), onay kayıtları (aşağıda). **r3 (AIW-9):** K5 yerel-öncelikli omurga ve K6 STATUS.md/risk kademeli PR süreci (ayrıntı: `ARCHITECTURE.md`, `STATUS_TEMPLATE.md`). Kişisel değer yok.

## Tek cümle
EJS, kullanıcının kriterlerine uyan Avrupa iş ilanlarını şirket-önce keşfeder, eler, doğru CV ve onaylı yanıtlarla başvurur (nihai Submit koşullu otomatik) ve sonucu izler; kullanıcıya yalnız gerçekten insan gerektiren anlarda (CAPTCHA/MFA, profilde karşılığı olmayan soru, belirsiz sonuç) ve günde en fazla bir özetle yazar. **Yanlış başvuru yapmaktansa başvurmaz.**

## Bağlayıcı kullanıcı kararları (21 Kasım 2026)
| # | Karar | Sonuç |
|---|---|---|
| K1 | Nihai Submit **koşullu otomatik** | Kill switch, tek-gönderim ledger'ı, bilinmeyen şema/CAPTCHA/MFA'da durma korunur. Sheet "Submit Boundary: USER-required" kuralının güncellenmesi (ARCHIVE_PLAN §5): KULLANICI ONAYLI (yorum `3a64b389…`, 07:24Z); uygulama ayrı PR/kullanıcı eylemi + merge onayı; o zamana kadar Sheet kuralı geçerli |
| K2 | Hacim önceliği | Sponsorluk Unknown dahil **Opportunity ≥65** → otomatik başvuru; sert engeller eler; 30 başvuruda kalibrasyon incelemesi |
| K3 | Takip = `OutcomeSource` portu | (a) Outcome Event Log CSV/Sheet içe aktarımı, (b) ileride Gmail/IMAP adaptörü |
| K4 | Kaynaklar | Şirket-önce + resmi kaynaklar varsayılan; LinkedIn/Indeed otomatik toplu çekim **KAPALI**; LinkedIn yalnız URL çözme; EURES otomasyon dışı |

| K5 | **Operasyon omurgası = yerel-öncelikli çekirdek** (kart 0967211d) | SQLite ledger tek doğruluk kaynağı; kurallar repo'da sürümlü YAML; tek LaunchAgent zinciri; Sheet isteğe bağlı **salt-okunur** dışa aktarım (Sheet'e yazma yok); claude.ai Daily kademeli kapatılır; Mac açık/oturum açık varsayımı, catch-up ayrı iş kalemi; yeni ücretli hizmet/bulut çalıştırıcı yok. CLAUDE.md "operasyon = Sheet" kuralını değiştirir: KULLANICI ONAYLI (yorum `3795ccd7…` 07:22Z, `3a64b389…` 07:24Z); uygulama ayrı PR + kullanıcı merge onayı gerektirir; o zamana kadar mevcut CLAUDE.md geçerli |
| K6 | **Süreç = tek `STATUS.md` + risk kademeli PR akışı** | Issue #79 / Drive 02_Guncel_Durum / Sheet durumu yerine STATUS.md; yüksek riskli yollarda tam akış, düşük riskli yollarda CI+Codex QA ile haftalık toplu merge onayı. CLAUDE.md "Değişiklik akışı"nı değiştirir (`STATUS_TEMPLATE.md`): KULLANICI ONAYLI (yorum `3795ccd7…`, kart 0967211d `light_process`; `3a64b389…`); uygulama ayrı PR + kullanıcı merge onayı gerektirir; o zamana kadar mevcut kural geçerli |

## Hat
`Keşif → Triage → CV/koşul seçimi (manifest) → ATS adaptörü (engine) → Takip (OutcomeSource) → Bildirim` — tek LaunchAgent zincirinde (discover→triage→manifest→engine→outcome→notifier), ledger'a yazan modüller; bkz. `ARCHITECTURE.md`.

| Adım | Ne yapar | Yöntem | Bugün |
|---|---|---|---|
| 1 Keşif | **Employer Watchlist** (sponsor-capable şirketler) → şirketin kariyer/ATS sayfası → ilan listesi; resmi kaynaklar; LinkedIn yalnız URL→ATS çözme | Deterministik, ağ taşıyıcısı enjekte edilebilir; ATS public API'leri (varsa) | YOK (kapalı Claude Daily görevi; Sheet'e elle) |
| 2 Triage | Repo YAML kurallarından (Sheet kuralları koda taşınır): OPPORTUNITY-1.0 skoru, sert engeller, `auto_apply` kararı, neden kodu; belirsiz → `needs_user` | Kural tabanlı. AI yalnız abonelik kotasında ve yalnız **sıralama**; karar vermez; ücretli API yok | YOK (kural Sheet'te) |
| 3 CV/koşul seçimi | Sheet "CV seçim kuralı" (3 baz CV + Master fallback) + yanıt bankası; manifest (EJS-EXECUTION-MANIFEST-1) | Deterministik; bilinmeyen alan boş | Doğrulama kapısı var, seçici YOK |
| 4 ATS adaptörü | URL→ATS sınıflandırıcı→adaptör; formu doldurur, CV yükler, koşullu otomatik tek Submit | Mevcut motor çekirdeği (ledger, kapılar, kill switch) | Yalnız SmartRecruiters (canlı kabul doğrulanmadı); ADP canary |
| 5 Takip | **`OutcomeSource` portu** → deterministik sınıflama → durum makinesi → ledger | Aşağıda | Sheet'te claude.ai/Gmail ile elle-otomatik (41 olay); kodda YOK; devir: 3a CSV, 3b Gmail/IMAP (ARCHITECTURE §4) |
| 6 Bildirim | Olay başına 1 + günlük ≤1 özet | Yerel | YOK |

Desteklenmeyen ATS ve profilde karşılığı olmayan form sorusu hattı tıkamaz: ilan `needs_user` kuyruğuna düşer, atlanır, eksikler **toplu** sorulur.

### OutcomeSource portu (K3)
```
OutcomeSource.fetch(since_cursor) -> Iterable[OutcomeEvent]
OutcomeEvent = {canonical_event_id (Gmail RFC Message-ID; kaynak türünden bağımsız),
                source_trace {adapter_kind (csv_import|sheet_export|imap|gmail), adapter_ref}, occurred_at,
                event_class, match_application_id | null, match_confidence, raw_ref}
```
- Adaptörler: `CsvOutcomeSource` / `SheetExportOutcomeSource` (Faz 3a, bugün; claude.ai'nin işlediği Outcome Event Log), `ImapOutcomeSource` / `GmailOutcomeSource` (Faz 3b; kullanıcının kendi OAuth uygulaması, yazılı izin, D2). Hepsi aynı portu doldurur; çekirdek hangi adaptörün çağırdığını bilmez.
- Sheet kuralları koda taşınır (ayrı iş kalemi): olay sınıfları (Application Received, Rejection, Interview, Offer, belirsiz…), eşleşme güveni (yüksek → otomatik uygula; düşük → `needs_user`), durum geçiş/çatışma kuralları, Offer güvenliği (Offer olayı asla otomatik kapatma/yanıt tetiklemez, yalnız bildirim).
- Durum geçişleri **yalnız ledger üzerinden** yapılır; adaptör ledger'a doğrudan yazmaz, `apply_outcome(event)` fonksiyonu yazar.

#### İki sistemin uyumlu çalışma sözleşmesi (claude.ai ↔ EJS)
1. **Kanonik olay kimliği (F1):** olayın kimliği kaynak türünden bağımsız `canonical_event_id`'dir (Gmail RFC Message-ID). Ledger'da **UNIQUE yalnız bu alandır**. Hangi adaptörün/taşıyıcının getirdiği ayrı `source_trace` kayıtlarında tutulur: `(canonical_event_id, adapter_kind, adapter_ref)` UNIQUE, bir olayın **birden fazla** izi olabilir. `canonical_event_id` olmayan/çıkarılamayan satır içe aktarılmaz; `import_rejected` raporuna ve `needs_user`'a düşer (tahmin yok).
   - *Kabul A:* aynı Message ID CSV'den sonra IMAP'ten (veya tersi) gelirse → **1** olay, **2** `source_trace` (`csv_import`, `imap`); olay sayısı ve durum değişmez.
   - *Kabul B:* aynı CSV iki kez içe aktarılırsa ledger ve izler değişmez (aynı `adapter_ref` no-op).
   - *Nitelik uyuşmazlığı (F2-R, sıra bağımsızlığı için):* aynı `canonical_event_id` için `occurred_at`, `event_class` veya `match_confidence` farklıysa olay `conflict` + `needs_user` olur (dışlanır, tahmin yok); `match_confidence` farklıysa da aynı şekilde `conflict` + `needs_user` olur (birleştirme/en düşüğü seçme yok: en-düşük-güven birleştirmesi, daha önce kaydedilmiş bir çatışmayı sonradan ortadan kaldırabildiği için bayrağın true→false olmasına yol açıyordu; spec_check P3 bunu yakaladı). Böylece çatışma bayrağı ingest'lerle kalkmaz.
   - *Kabul C:* aynı `canonical_event_id` farklı `event_class` ile tekrar gelirse (ör. CSV: Received, IMAP: Rejection) → **yeni olay açılmaz**; iz eklenir ve olay `conflict` + `needs_user` olur (madde 3). Çatışan olay durum hesabından dışlandığı için durum **kalan olay kümesinden yeniden hesaplanır** (madde 2'deki tek açık istisna): olay daha önce durumu yükseltmişse (ör. Received) durum önceki seviyeye (Applied) geri alınır ve bu geri alma yüzeye çıkar. Ara durumlar: yalnız Received gelmişken **Received/false**; çatışan Rejection izi eklenince **Applied/true** (ters sırada Rejected → Applied/true). Aynı nihai durum iki sırada da.
2. **Monoton durum — yalnız çatışmasız olaylar için (F2-R):** durum sırası Applied < Received/Acknowledged < Interview < Offer; red terminal ama tarihsel Applied silinmez. Çatışma yaratmayan hiçbir ingest (düşük sıralı, eski `occurred_at`'li, mükerrer, düşük güvenli olay dahil) durumu **geri düşürmez**; yalnız olay günlüğüne eklenir. **TEK AÇIK İSTİSNA (ara durum geri alma):** bir ingest md.3'teki anlamda bir çatışma *yaratırsa* (= çatışan `canonical_event_id` kümesine en az bir YENİ kimlik girerse; bu `conflict` bayrağının zaten true olduğu adımı da kapsar, yalnız ilk false→true geçişi değildir), çatışan olaylar durum hesabından dışlanır ve durum kalan olay kümesinden yeniden hesaplanır; sonuç önceki ara durumdan düşük olabilir. Küme büyümeyen hiçbir ingest durumu düşüremez (R6 tek kural; ROADMAP P2, ARCHITECTURE §4, YAML `rollback_trigger` aynıdır). Çatışma zaten true iken gelen ikinci/üçüncü geri alma da aynı kuralla yapılır (Kabul G). Bu geri alma sessiz değildir: başvuru `conflict` + `needs_user` olur ve ledger'a `state_rollback` kaydı (önceki durum, yeni durum, çatışan `canonical_event_id`'ler) yazılır. Çatışma yalnız kullanıcı çözümüyle kalkar (ingest kaldırmaz): `conflict` bayrağı ingest'ler boyunca asla true→false olmaz. Başka hiçbir yolla durum düşmez.
3. **Çatışma — TEK politika (F2; ARCHITECTURE §4 ve ROADMAP 3a/3b aynıdır):** güven yüksek olsa da **hiçbir sınıf otomatik kazanmaz** (kazanan-seçme istisnası yok; md.2'deki geri alma bir kazanan seçimi değil, çatışan olayların dışlanmasıdır) ("yüksek güvenli kazanır" kaldırıldı). Çatışma şunlardır: (a) aynı `canonical_event_id` farklı sınıfla; (b) aynı başvuruda, durum hesabına girebilecek (yüksek güvenli) olaylar arasında Rejection ile, `occurred_at`'i Rejection'dan **sonra** olan Received/Interview/Offer olayı (red sonrası ilerleme). Çatışan olaylar olay günlüğüne yazılır, başvuru `conflict` işaretlenir ve `needs_user`'a düşer; çatışan olaylar **durum hesabına katılmaz**. Eşleşme güveni düşük (yüksek değil) olay da durum hesabına katılmaz, `needs_user`'a düşer. Ledger yazımları tek-yazar kilidi/işlem içindedir.
   - **Sıra bağımsızlığı nasıl sağlanır:** nihai durum, "gelme sırası"nın değil, başvurunun **olay kümesinin** işlevidir: çatışmasız ve yüksek güvenli olaylar `occurred_at` (eşitse `canonical_event_id`) sırasıyla birleştirilir; çatışan olay kümesi kümeden belirlenir (sırayla değil). **Model A (seçilen):** durum, olay kümesinin işlevidir; ara durum da öyle (her ingest sonrası durum = o ana kadar gelen küme için işlev). Çatışan olay duruma etki etmez = durum çatışmasız alt kümeden hesaplanır; bu yüzden çatışma anında durum **dondurulmaz, yeniden hesaplanır** (md.2 istisnası). Model B (çatışma anında önceki durumu dondur) reddedildi: dondurulan durum gelme sırasına bağlıdır (E1→E2 ⇒ Received donar, E2→E1 ⇒ Rejected donar), kümeden türetilemez, sıra bağımsızlığını bozar. Her önek için işlev sabit olduğundan aynı küme her sırada aynı durumu ve bayrağı verir.
   - *Kabul D (ters sıra):* başvuru Applied. E1 = Received, `occurred_at` 5 Kas, yüksek güven. E2 = Rejection, `occurred_at` 3 Kas, düşük güven. Sıra E1→E2 de E2→E1 de: E2 düşük güvenli olduğundan hesaba katılmaz (dolayısıyla (b) çatışması oluşmaz), E1 çatışmasız ve yüksek güvenlidir → nihai durum **Received**, E2 `needs_user` kuyruğunda. İki sıra aynı sonucu verir. **Ara durumlar (her ingest sonrası):** E1→E2: Received/false → Received/false (E2 `needs_user`, düşük güven). E2→E1: Applied/false (E2 tek başına düşük güvenli, katılmaz; `needs_user`) → Received/false.
   - *Kabul G (zaten conflict=true iken ikinci geri alma, aynı başvuru, hepsi yüksek güven):* x=Received t=1; x=Interview t=1 (aynı kimlik/farklı sınıf); e1=Received t=5; e2=Rejection t=3. Her ingest sonrası (durum/conflict): **Received/false → Applied/true → Received/true → Applied/true**. 2. adım: x çatışır (küme {x}), `state_rollback` Received→Applied. 3. adım: e1 çatışmasız, yükseltir (düşüş yok). 4. adım: e2 ile e1 çatışır (küme {x} → {x,e1,e2}, yeni kimlik girdi) → bayrak zaten true olsa da ikinci `state_rollback` Received→Applied yazılır ve yüzeye çıkar. Dört girdinin 24 permütasyonunun nihai sonucu aynıdır: Applied/true.
   - *Kabul E (iki yüksek güven, çatışma):* E1 = Received 5 Kas, E2 = Rejection 3 Kas, ikisi de yüksek güven (red sonrası ilerleme, (b)). İki sıra da: nihai durum **Applied** (ikisi de hesaba katılmaz), `conflict` + `needs_user`. **Ara durumlar:** E1→E2: **Received/false → Applied/true** (E2 gelince `state_rollback` Received→Applied; md.2'nin tek açık istisnası). E2→E1: **Rejected/false → Applied/true** (`state_rollback` Rejected→Applied). Önek tabloları iki sırada farklıdır; nihai (Applied/true) aynıdır.
   - *Kabul F (normal ilerleme):* E1 = Received 5 Kas, E2 = Rejection 9 Kas, ikisi de yüksek güven → çatışma değil; nihai **Rejected**. **Ara durumlar:** E1→E2: Received/false → Rejected/false; E2→E1: Rejected/false → Rejected/false (E1 eski olay, durumu düşürmez, `state_rollback` yok).
4. **Kaynak izi:** her olayın `source_trace` kayıtları saklanır; hangi sistemin/taşıyıcının yazdığı ayırt edilebilir. İçe aktarım idempotenttir (Kabul B).
5. **Geçiş dönemi:** claude.ai takibi sürdüğü sürece EJS yalnız `CsvOutcomeSource` ile okur ve Sheet'e **yazmaz** (K3/K5). Faz 3b'de Gmail/IMAP devralınca kesme tarihi + örtüşme penceresi + UNIQUE kimlik kuralları `ARCHITECTURE.md` §4'tedir; claude.ai Daily'yi kapatmak kullanıcı eylemidir.

## Profil yanıt bankası (karar: AIW-6 §9.2)
Sheet'in "Application Answer Library" (24 soru: Auto-safe 12, Dynamic AI 5, USER-required 4, USER-only 3) ve "User Fact Registry" (21 olgu: 16 Verified, 4 Pending, 1 USER-only) bu bankanın kaynağıdır; koda taşınırken yalnız **Verified** olgular otomatik kullanılır. Karşılığı yoksa: ilan atlanır, eksik toplu sorulur. **Tahmin yok.** Çalışma izni, sponsorluk, maaş, hukuki, demografik yanıtlar yalnız kullanıcı onaylı değerdir. "Dynamic AI" sınıfı kural tabanlı-önce ilkesiyle çelişmesin diye yalnız kota içi taslak üretir ve kullanıcı onaylı şablona bağlanmadıkça gönderilmez (ÖNERİ, onay bekler).

## Tek doğruluk kaynağı — yerel-öncelikli omurga (K5; yön kullanıcı onaylı; CLAUDE.md değişikliği ayrı PR + merge onayı bekler)
Sheet iki rol taşıyor: (1) **kural kaynağı** (ağırlık, eşik, CV kuralı, yanıt bankası), (2) **operasyon görünümü/kaydı** (Applications, Outcome Log). Öneri:
- **Kurallar:** Sheet → repodaki `criteria.yaml` + kural modülleri (sürümlü, testli; ör. OPPORTUNITY-1.0). Koda taşındıktan sonra tek kaynak repo; Sheet kural sekmeleri salt-okunur/arşiv.
- **Operasyonel durum:** yerel SQLite ledger **tek doğruluk kaynağı**; Sheet yalnız **isteğe bağlı salt-okunur dışa aktarım** (ledger → CSV; **Sheet'e yazma yok**, r2'deki "kullanıcı onaylı Sheet yazımı" kaldırıldı). Şema: `ARCHITECTURE.md` §5.
- **Durum/yönetişim özeti:** Issue #79, Drive 02_Guncel_Durum ve Sheet durumu yerine repo'da tek `STATUS.md` (K6, `STATUS_TEMPLATE.md`).
- **Geçiş dönemi (şimdi):** claude.ai'nin yazdığı Outcome Log (Sheet'te) **girdi**; ledger'a `OutcomeSource` ile içe aktarılır, çatışma kuralı yukarıdaki gibi. Bu, mevcut CLAUDE.md kuralı ("operasyon = Sheet") ile **çelişir**; onaylanana kadar CLAUDE.md geçerli. Alternatif (kullanıcı K5 ile seçmedi): Sheet kalıcı kaynak kalır, ledger önbellek olur → çift yön senkron karmaşası; önerilmez.

## Başarı ölçütü
Başlangıç değerleri (EU Job Tracker dışa aktarımı, 27 Temmuz–16 Kasım 2026; Sheet formülleri/Dashboard doğrulanmadı, ham hücre değerleri):
| Ölçüt | Başlangıç (baseline) | Hedef | Ölçüm yöntemi |
|---|---|---|---|
| Başvuru hacmi | ~16 başvuru / ~4 ay (12'si tek gün, 27 Temmuz) | `<HEDEF_SAYI/HAFTA: kullanıcı belirler>` | Ledger: Applied (teyit kanıtlı) / hafta |
| Red oranı | 13/16 red (Outcome Log'da 19 red olayı) | `<HEDEF: kullanıcı belirler>` | Rejected / Applied, `apply` kohortuna göre |
| Mülakat | 0 (tüm kaynaklarda Interviews=0) | `<HEDEF: kullanıcı belirler>` | Interview olayı / Applied; kohort: eşik 65–74 vs ≥75 |
| Yanıt oranı | henüz hesaplanmadı | `<HEDEF>` | Received+Rejection+Interview / Applied |
| Başvuru başına kullanıcı dakikası | ölçülmedi (Manual Review: aktif 64 satırın 34'ü sponsorluk Unknown, 25 Auto Apply'dan yalnız 2'si To Apply → darboğaz insan kuyruğu) | `<HEDEF_DAKİKA: kullanıcı belirler>` | Kullanıcı müdahale olay damgaları / başvuru |
| Yanlış başvuru | 0 | **0** (yanlış ilan, yanlış CV, uydurma yanıt) | Her Applied için kanıt + yanıt kaynağı denetimi |

### Açık risk — K2 (max_volume) eşiği
K2, sponsorluk Unknown dahil Opportunity ≥65'e otomatik başvuru der. Mevcut veri (13/16 red, mülakat 0; Sheet'in eski AUTOREVIEW-1.0 eşiği daha sıkıydı: 75/70, Unknown'da 80/78) bu gevşetmeyle **red oranının artabileceğini** gösteriyor; sponsorluk Unknown 56/110 ilan. Önlemler: (0) kalibrasyon sayacı günlük özette ve STATUS.md §2'de görünür; (1) sert engeller (açık "sponsorluk yok", zorunlu dil, kapalı ilan, aşırı junior/teknik-only) hâlâ eler; (2) **30 otomatik başvuru sonunda kalibrasyon incelemesi** (red/yanıt/mülakat, eşik kohortlarına göre); (3) kullanıcı eşiği istediği zaman değiştirebilir; (4) hacim, "yanlış başvuru=0" kısıtının **altındadır** — yüksek red oranı "yanlış başvuru" sayılmaz ama kalibrasyon tetikleyicisidir. Önerilen otomatik duraklatma eşiği (onay bekler): ilk 30 başvuruda ≥ `<KULLANICI_BELİRLER>` red ve 0 yanıt ise otomatik gönderim `needs_user`'a düşer.

Maksimizasyon sırası: yanlış başvuru=0 kısıtı → kullanıcı dakikası → hacim (K2 ile hacim ağırlığı yükseldi, kısıt değişmedi).

## Kapsam dışı (şimdilik)
Yeni ücretli hizmet, bulut çalıştırıcı (kullanıcı seçmedi; Mac açık/oturum açık varsayımı), Sheet'e yazma. Ücretli API/model, CAPTCHA/MFA atlatma, LinkedIn/Indeed otomatik toplu çekim ve LinkedIn Easy Apply, EURES otomasyonu (Sheet kuralı: toplu kazıma yok), otomatik e-posta cevabı yazma/gönderme, kullanıcı adına hesap açma/şifre girme. (r1'den fark: Workday artık kapsam içi, Faz 2.)

## Değişmeyenler
Güvenlik kuralları aynen (ARCHIVE_PLAN §3). Sessiz arıza kabul edilmez: boş kuyruk `QUEUE_EMPTY` nedeniyle, ölü besleyici uyarıyla, her gün özetle görünür (ARCHITECTURE §3). `runtime.json`/`STOP` yalnız kullanıcıda; fail-closed; "Merged ≠ canlı çalıştı".

## Açık / doğrulanmadı
- Doldurulmuş kriter taslağı (`AIW-6/criteria.draft.yaml`) kullanıcı onayı bekler.
- Başarı ölçütü hedefleri kullanıcıdadır; baseline değerleri Sheet'in ham hücrelerinden.
- ATS public API varlığı bilgi tabanından; ağdan doğrulanmadı. Sheet'in canlı sürümü görülmedi (16 Kasım dışa aktarımı). SmartRecruiters canlı kabulü, açık PR/CI/Issue #79 okunmadı. LinkedIn/Indeed ToS yorumu hukuki görüş değildir.
