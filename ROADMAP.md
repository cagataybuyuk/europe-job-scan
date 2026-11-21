> **Güncelleme (21 Kasım 2026):** Kullanıcı kararıyla (Paperclip görevi AIW-6, kart `5c2ade95` "Her yolda doğrudan ben") "bağımsız inceleme" ve "PR başına kullanıcı merge onayı" koşulları kaldırıldı; CI yeşilse ajan tüm yollarda birleştirebilir (`CLAUDE.md` "Değişiklik akışı"). Bu belgede (özellikle K6, D8/D9, "yüksek/düşük risk" etiketleri, "haftalık toplu onay", "tam akış" ve R0–R3 kapılarındaki "kullanıcı onayı" ifadeleri) PR/merge sürecine dair aksini söyleyen yerler tarihsel taslak metnidir; geçerli kural `CLAUDE.md`'dir. Gönderim rampasının (R0–R3) kademe geçişleri, `runtime.json`/`STOP` ve güvenlik sınırları yalnız kullanıcıda kalır.

# EJS Yol Haritası (TASLAK r6, Faz 0 revizyon 5 / AIW-17)

**r6 (AIW-17, F2-R2):** geri alma koşulu tek kural: çatışan kimlik kümesine yeni kimlik girdiği HER ingest adımı (conflict zaten true olsa da); P2 ve PRODUCT/ARCHITECTURE/YAML aynı; kabul fixture G (ikinci geri alma). **r5 (AIW-14, F2-R):** ara durum geri alma modeli = Model A (durum olay kümesinin işlevi; çatışmada çatışan olaylar dışlanıp yeniden hesap = tek açık istisna, `conflict`+`needs_user`+`state_rollback`); kabul testleri her ingest sonrası ara durumu da sınar. **r4 (AIW-11):** F1 kanonik olay kimliği + `source_trace` (3a.2/3a.5/uyum kriterleri), F2 tek çatışma politikası, F3 ilk STATUS.md fazi (aşağıda), D8/D9 kullanıcı onaylı kaydı, sayı kaynakları. **r3 (AIW-9) değişenler:** yerel-öncelikli omurga (K5: ledger tek kaynak, YAML kurallar, tek LaunchAgent zinciri, Sheet salt-okunur dışa aktarım, claude.ai kademeli kapatma → Faz 3a.5/3b.2, 1.8), Mac catch-up iş kalemi (2.10), STATUS.md + risk kademeli PR akışı (K6; **ÖNERİ**, Faz 0 ve 0.5). Ayrıntı: `ARCHITECTURE.md`, `STATUS_TEMPLATE.md`.

Kaynaklar: AIW-6 §6/§11, `tracker_findings.md`, AIW-5 §8 (P0/P1/P2). Her faz ayrı onaylı PR görevidir: branch→PR→exact-head CI→bağımsız inceleme (Codex QA Reviewer)→kullanıcı merge onayı. **Mevcut kural budur ve onaya kadar geçerlidir.** K6 (KULLANICI ONAYLI; uygulama ayrı PR + merge onayı, o zamana kadar mevcut akış geçerli): gönderim/güvenlik/ledger/runtime.json yollarına dokunan PR'larda bu tam akış kalır; keşif/triage/belge gibi gönderimi etkilemeyen PR'larda CI yeşil + Codex QA onayıyla merge onayı haftalık toplu olabilir (yol listesi: `STATUS_TEMPLATE.md` §B). Aşağıdaki "yüksek/düşük risk" etiketleri bu öneriye göredir. Yürütme: Claude Development. Test ortamı notu: ajan HOME'u yönlendirildiği için `PLAYWRIGHT_BROWSERS_PATH=/Users/cagataybuyuk/Library/Caches/ms-playwright` gerekir (AIW-5 §12).

r1'den (AIW-7) değişenler: Employer Watchlist keşfi (Faz 1); Sheet kurallarının koda taşınması ayrı iş kalemleri; Workday + employer custom form Faz 4'ten Faz 2'ye; Outcome Tracking OutcomeSource portuyla Faz 3a/3b'ye bölündü; gönderim açılışı için kademeli rampa + onay kapıları.

## Bağımlılıklar
| Bağımlılık | Durum | Engellediği |
|---|---|---|
| D1 Doldurulmuş kriter dosyası | Taslak var (`AIW-6/criteria.draft.yaml`), **kullanıcı onayı bekliyor** | Faz 1 triage canlı değerleri |
| D2 Gmail/IMAP izni (kullanıcının kendi OAuth uygulaması) | Verilmedi; Paperclip Gmail OAuth Google tarafından engellendi | Yalnız Faz 3b (3a'yı engellemez) |
| D3 Profil yanıt bankası içeriği (Sheet Answer Library/User Fact Registry'den; yalnız Verified) | Kullanıcı onayı bekliyor | Faz 2 canlı başvuru |
| D4 AI sıralama kotası | Önce kural tabanlı | Faz 4, opsiyonel |
| D5 SmartRecruiters canlı kabulü (kullanıcı onaylı) | Doğrulanmadı | Faz 2 |
| D6 Employer Watchlist (72 satır, Sheet) CSV dışa aktarımı, kişisel veri çıkarılmış | Kullanıcı sağlar; repo dışı owner-only | Faz 1 keşif |
| D7 Outcome Event Log CSV dışa aktarımı (claude.ai'nin işlediği) | Kullanıcı sağlar | Faz 3a |
| D8 Sheet "Submit Boundary" güncelleme önerisinin kullanıcı kararı (ARCHIVE_PLAN §5) | KULLANICI ONAYLI (AIW-6 görevinde sunucu kaydı olan yorumlar: id `3795ccd7-f452-427c-9398-76f1a3904b5e`, 21 Kasım 2026 07:22Z + kart 0967211d yanıtı `local_first`/`light_process`; Sheet kuralı güncellemesi: yorum `3a64b389-1f39-4e87-a9df-10fb91ca25ce`, 07:24Z). **Uygulama ayrı PR/kullanıcı eylemi + kullanıcı merge onayı gerektirir; onay merge onayı değildir; o zamana kadar mevcut Sheet kuralı ve R0/R1 geçerli.** | Faz 2 gönderim rampası R2+ |
| D9 K5/K6 CLAUDE.md değişikliklerinin (kaynak önceliği, Değişiklik akışı) kullanıcı onayı | KULLANICI ONAYLI (AIW-6 görevinde sunucu kaydı olan yorumlar: id `3795ccd7-f452-427c-9398-76f1a3904b5e`, 21 Kasım 2026 07:22Z + kart 0967211d yanıtı `local_first`/`light_process`; CLAUDE.md değişikliği ve Sheet Submit kuralı güncellemesi: yorum id `3a64b389-1f39-4e87-a9df-10fb91ca25ce`, 07:24Z). **Uygulama ayrı PR + kullanıcı merge onayı gerektirir; onay merge onayı değildir; o zamana kadar mevcut CLAUDE.md akışı ve Sheet kuralı geçerlidir.** | Faz 0.5, STATUS.md'nin resmî kaynak olması, düşük risk toplu merge |
| D10 Mac açık/oturum açık varsayımı + enerji ayarı (kullanıcı) | Varsayım | 2.10 catch-up kabulü |

## Faz 0 — Belge ve temizlik (yalnız belge)
- İçerik (r3 ekler: `ARCHITECTURE.md`, `STATUS_TEMPLATE.md` repo'ya girer): tek ürün tanımı, tek roadmap, belge arşivi, "değişmez kurallar" 1 sayfa, tek doğruluk kaynağı kararı, Sheet sözleşme sekmeleri ↔ docs sadeleşmesi (ARCHIVE_PLAN §4).
- Kabul: (1) `docs/` içindeki her belge sınıflı; (2) arşive taşınan belgelere giden tüm referanslar (CI path filtreleri, README, CLAUDE.md, `tests/node/gd004_gateway.test.cjs`) güncel; (3) `pytest` çıkış 0 ve CI yeşil (exact-head); (4) hiçbir kod/davranış değişmedi; (5) kullanıcı merge onayı.
- Risk: belge PR'ı düşük; ama `CLAUDE.md`/`INVARIANTS.md` değişikliği **yüksek** (tam akış) — K5/K6 kuralları CLAUDE.md'ye ancak kullanıcı onayından sonra, ayrı tam-akışlı PR'la işlenir.
- Bağımlılık: yok (K5/K6'nın CLAUDE.md'ye işlenmesi D9). Eşleme: AIW-5 P1-3 (kısmen).

## Faz 0.5 — Süreç ve omurga iskeleti (K5/K6; D9 *uygulaması* = ayrı CLAUDE.md PR'ı merge olduktan sonra)
- 0.5.1 **İlk resmî `STATUS.md`** (repo kökü; Faz 0'da yalnız `STATUS_TEMPLATE.md` şablonu `docs/`'a girer, resmî STATUS.md Faz 0'da OLUŞMAZ) — CLAUDE.md PR'ı (D9 uygulaması) merge olduktan sonra + Issue #79/Drive/Sheet durumunun STATUS.md'ye taşınması (2 hafta yan yana).
- 0.5.2 Risk etiketleme betiği/CI (`risk:high|low` değişen dosyalardan; **yüksek riskli**, `.github/workflows` dokunur) + `CODEOWNERS`.
- 0.5.3 Ledger erişim katmanı (tek yazar, transaction), `run_id`/`health` tabloları ve `run-chain` iskeleti. Yüksek risk (ledger/LaunchAgent).
- Kabul: etiketleyici yüksek yol örneklerinde `high` basar, belirsizde `high`; yazar etiketi düşüremez (negatif test); ledger'a yalnız erişim katmanı yazar (statik test); `pytest` çıkış 0, exact-head CI yeşil.

## Faz 1 — Kuyruğu besle (gönderim yok)
İş kalemleri (her biri ayrı küçük PR olabilir):
- **1.1 Kriter şeması + yükleyici** (`CRITERIA_TEMPLATE.yaml` r3; ağırlık toplamı=100 doğrulaması; tüm kurallar repo'da sürümlü YAML, `rule_version` ledger'a yazılır). Risk: şema düşük; `auto_apply`/`cv_selection`/`answer_bank` kuralları yüksek.
- **1.2 Employer Watchlist tabanlı şirket-önce keşif:** watchlist (repo dışı) → şirketin kariyer/ATS sayfası → ilan listesi; URL→ATS sınıflandırma; ATS public JSON API'si olanlar (Greenhouse/Lever/SmartRecruiters/…; ağdan doğrulanmadı) tarayıcısız, olmayanlar kariyer sayfası okuyucusu. Gerekçe (tracker): Direct Employer Career Pages en verimli kaynak (57 ilan/12 başvuru); LinkedIn 24/5; Glassdoor/WTTJ/StepStone/IrishJobs/EURES 0 ilan.
- **1.3 LinkedIn yalnız URL çözme** (`linkedin_live_resolver` yeniden kullanım; LinkedIn kaynaklı ~14 aktif ilan (doğrulanmadı; `tracker_findings.md`'de yok) dış ATS'e yönlenir). Toplu çekim yok (K4).
- **1.4 OPPORTUNITY-1.0 skoru koda:** ağırlıklar sponsorluk 30, maaş/yaşam standardı 25, rol uyumu 20, kapsam 10, kariyer 7, İngilizce 5, kıdem 3; eşikler 85 Priority Apply / 75 Apply / 65 Consider / <65 Skip. Ağırlık/eşik `criteria.yaml`'dan gelir, kodda sabit değil.
- **1.5 AUTOREVIEW kuralı koda (K2 sürümü):** `auto_apply` = Opportunity ≥65 (Unknown dahil) + sert engeller → Auto Skip; eski Sheet kuralı (75/70; Unknown'da 80/78) `previous_sheet_rule` olarak kayıtlı; gri bölge `needs_user`. Karar yalnız **etiket** üretir (bu fazda gönderim yok).
- **1.6 Tekilleştirme** (`company + normalized role + location` + ledger'a karşı), `QUEUE_EMPTY` teşhisi (neden sınıfları: feeder_dead/all_filtered/awaiting_user_backlog/paused), "besleyici ölü" uyarısı, günlük özet (ARCHITECTURE §3). Risk: özet/notifier düşük; motorun boş-kuyruk çıktısı yüksek (`engine_queue`).
- **1.7 Shadow karşılaştırma:** aynı girdiyle EJS kararı ↔ Sheet'in Auto Review sütunu; fark raporu (Sheet kuralının doğru taşındığının kanıtı).
- **1.8 Salt-okunur dışa aktarım** (`export/*.csv`, ARCHITECTURE §5; Sheet'e yazma kod yolu yok — negatif test). Düşük risk.
- Kabul: offline fixture'dan ≥10 ilan normalleşir, tekrarlar elenir; ağ taşıyıcısı enjekte, testte gerçek ağ = hata; aynı girdi→aynı karar; belirsiz→`needs_user`; çalışma izni asla tahmin edilmez; 1.4/1.5 için Sheet'ten türetilmiş sentetik vaka tablosu (sınır değerleri 64/65, 74/75, 84/85) test edilir; çıktı yalnız repo-dışı kuyruk, 0600; boş kuyruk → `QUEUE_EMPTY`+neden ("DONE" değil); discover `EŞİK_SAAT` boyunca çalışmazsa saat-enjekteli testte `feeder_dead` bildirimi; günlük özet "hiçbir şey olmadı" gününde de üretilir; LinkedIn/Indeed/EURES için toplu çekim kod yolu **yok** (negatif test); canlı gönderim yok.
- Bağımlılık: D1, D6 (yoksa yalnız altyapı + fixture). Eşleme: **P0-1, P0-2, P1-4**.

## Faz 2 — Başvuru hacmi
İş kalemleri:
- **2.1 Profil yanıt bankası** (+ "profil eksiği" toplu sorusu); Sheet Answer Library sınıfları (Auto-safe / Dynamic AI / USER-required / USER-only) koda; yalnız Verified olgular.
- **2.2 CV seçici:** Sheet CV seçim kuralı (3 baz CV + Master fallback); `cv_asset_gate.py` ile doğrulama.
- **2.3 Manifest üretici** + tek-komut zincir.
- **2.4 URL→ATS sınıflandırıcı** (`ats_adapter_registry.py`) + `needs_user` düşüşü.
- **2.5 SmartRecruiters canlı kabulü** (D5).
- **2.6 Workday adaptörü** ve **2.7 "employer custom form" adaptörü** (genel form-şema tespiti; bilinmeyen şemada dur).
- **2.8 Greenhouse + Lever adaptörleri.**
- **2.9 Gönderim açılış rampası** (aşağı).
- **2.10 Mac uyku/kapalı catch-up** (ARCHITECTURE §2): zincir başında `last_successful_run` kontrolü, kaçan günler için tek telafi turu, günlük sınır biriktirilmez, "son çalışma X saat önce" uyarısı; kullanıcı Mac'i açık/oturum açık tutar (D10). Yüksek risk (LaunchAgent). Kabul: saat enjekteli testte 2 gün kaçan zincir → 1 telafi turu, sınır aşılmaz; kilitle çift zincir yok. Eşleme: P2-4 (3a'dan öne alındı).
- Kabul: manifest doğrulayıcıdan geçer; bilinmeyen alan boş; karşılıksız soru → ilan atlanır + eksik listelenir; her gönderilen yanıtın kaynağı belli; her adaptör fixture'a karşı inspect→fill→upload→dur testli; yanlış başvuru=0 negatif testleri (yanlış CV, yanlış ilan, tahminli yanıt); Workday/custom form'da login/hesap açma/CAPTCHA/MFA → durur.
- Bağımlılık: D3, D5, D8, Faz 1. Eşleme: **P0-3, P1-3, P1-5**, P2-1.

### Sıralama gerekçesi (Workday/custom form öne alındı)
Tracker verisi (kaynak: `work/AIW-6/tracker_findings.md` §2 — yalnız şu sayılar orada VAR: aktif 64'te Workday 5, SmartRecruiters 5; tüm 110 fırsatta URL ailesi employer/diğer 61, LinkedIn 21, Workday 7, SmartRecruiters 7; **"aktif 64'te employer/özel 31" ve "LinkedIn yönlendirme 14" `tracker_findings.md`'de BULUNMUYOR** — AIW-8 ROADMAP'inden devralınmış, ham xlsx açılmadan **doğrulanmadı**, yaklaşık değer olarak okunmalı): aktif 64 ilanda employer/özel ~31, LinkedIn yönlendirme ~14, Workday 5, SmartRecruiters 5 (geri kalan Greenhouse/Lever/Workable vb. küçük paylar; tüm 110 fırsatta URL ailesi: employer/diğer 61, LinkedIn 21, Workday 7, SmartRecruiters 7, Greenhouse 2, Lever 2). Not: bunlar çoğunlukla **keşif sayfası** ailesidir, başvuru ucu değildir; kesin form ailesi Faz 1.3 çözümlemesinden sonra yeniden sayılmalıdır (doğrulanmadı).
- Greenhouse/Lever tracker'da toplam yalnız 4 fırsat; r1'in "Greenhouse/Lever önce" sırası **veriyle desteklenmiyor**. Hacim (K2) hedefi için kapsama en çok katkı: employer custom form (~31, doğrulanmadı) > LinkedIn→dış ATS çözümü (~14, doğrulanmadı; Faz 1.3 ile gelir) > Workday = SmartRecruiters (5+5).
- Motor yalnız SmartRecruiters'ı biliyor; Workday ve custom form motorun ilk gerçek genelleme sınavıdır → erken öğrenmek için Faz 2'ye alındı.
- **Risk (açık):** Workday çoğu tenant'ta hesap/oturum ister; değişmez kural "hesap açma/şifre girme kullanıcıya aittir". Bu yüzden Workday kapsamı: kullanıcının önceden oluşturduğu oturum + inspect/fill + durma; yoksa `needs_user`. Workday kapsama katkısı bu kısıtla sınırlı olabilir. Custom form'lar heterojen; adaptör "tanıdığı şemada doldur, tanımadığında dur" olacak, beklenen otomasyon oranı ölçülene kadar **doğrulanmadı**.
- Greenhouse/Lever Faz 2'nin sonuna (2.8) kaldı: ucuz ve standart, ama hacim payı düşük.

### Kademeli gönderim rampası (ÖNERİ, her kapı kullanıcı onayı ister)
| Kademe | Davranış | Geçiş kapısı |
|---|---|---|
| R0 Gölge | Karar+manifest üretilir, **gönderim yok**; EJS kararı ↔ Sheet karşılaştırılır | Fark raporu kullanıcıca kabul; **kullanıcı onayı** |
| R1 Pilot | İlk `<PİLOT_N, öneri 3–5>` başvuru, her biri formu doldurup **Submit öncesi durur**, kullanıcı onaylar/tıklar | 0 yanlış başvuru; **kullanıcı onayı** |
| R2 Koşullu otomatik, düşük sınır | `submit_mode=conditional_auto`, günlük sınır `<GÜNLÜK_SINIR, öneri 3>`; ledger/kill switch/dur kuralları; tek ATS ailesiyle başla | 30 başvuruda **kalibrasyon incelemesi** (red/yanıt/mülakat, eşik kohortları; PRODUCT riski); **kullanıcı onayı** |
| R3 Genişletme | Sınır/ATS ailesi artışı kullanıcı kararıyla | Her artış için onay; yeni ATS = yeni pilot |
Otomatik geri çekilme: tek bir yanlış başvuru, beklenmeyen şema veya kill switch → R1'e düşer. `runtime.json`/`STOP` yalnız kullanıcıda; kademe geçişini kullanıcı yapar.

## Faz 3 — Takip ve bildirim (OutcomeSource) ve claude.ai'den devir
Sıra: 3a.5 devir aşama 1 → 3b.1/3b.2 aşama 2 (claude.ai Daily kademeli kapatma; iş→modül tablosu ARCHITECTURE §4).
**3a (D2 gerekmez, Faz 1'den sonra Faz 2 ile paralel başlayabilir):**
- **3a.1 `OutcomeSource` portu + `OutcomeEvent` modeli + `apply_outcome` (ledger tek yazar yolu).**
- **3a.2 `CsvOutcomeSource`:** claude.ai'nin işlediği Outcome Event Log dışa aktarımı (D7); Gmail Message ID'yi `canonical_event_id` olarak kullanır; CSV satırı `source_trace` (`csv_import`) olarak kaydedilir, tekilleştirme yalnız kanonik kimlikte (PRODUCT md.1).
- **3a.3 Outcome Tracking kuralları koda:** olay sınıfları, eşleşme güveni eşikleri, durum geçiş/çatışma kuralları, Offer güvenliği (Offer otomatik eylem tetiklemez).
- **3a.5 claude.ai→yerel devir (aşama 1):** claude.ai Gmail takibini sürdürür, EJS CSV okur; `source_trace.adapter_kind` ayrımı (kanonik kimlik kaynaktan bağımsız); geçişte çift bildirim etiketi (ARCHITECTURE §4). Kullanıcı eylemi: Daily kapatma kararı.
- **3a.4 Bildirim** (olay başına 1, günlük ≤1 özet; içerikte aday verisi yok), tracker TEST entegrasyonu, catch-up.
- Uyum kabul kriterleri (PRODUCT "claude.ai ↔ EJS sözleşmesi" md.1–4 — tek politika): (i) aynı Gmail Message ID iki kez → ledger ve olay sayısı değişmez (idempotent, CSV iki kez içe aktarma dahil); (i-b) **aynı Message ID CSV ve IMAP'ten** → 1 olay + 2 `source_trace`; (i-c) **aynı Message ID farklı sınıfla** → yeni olay yok, `conflict`+`needs_user`; çatışan olay dışlandığı için durum kalan kümeden yeniden hesaplanır (Received+Rejection izi → Applied/true, ters sırada Rejected → Applied/true), `state_rollback` kaydı yazılır (PRODUCT md.2'nin tek açık istisnası; "durum değişmez" yalnız çatışma yaratmayan ingest için geçerlidir); (ii) Interview/Offer sonrası gelen eski/düşük sınıflı **çatışmasız** olay durumu geri düşürmez; durum yalnız çatışma yaratan ingest'te düşebilir (geri alma, `state_rollback`); (iii) çatışma politikası: güven yüksek olsa da hiçbir sınıf otomatik kazanmaz; çatışan olay duruma katılmaz, `conflict`+`needs_user` (PRODUCT Kabul D/E/F); (iv) sıra bağımsızlığı ve **her ingest sonrası ara durum**: özellik testi — Kabul D/E/F ve (i-c) için tüm olay permütasyonları aynı son durumu ve `conflict` bayrağını verir; ayrıca **her önek** (her ingest sonrası) için beklenen (durum, conflict) tablosu sınanır (PRODUCT Kabul D/E/F ara durumları): E: Received/false→Applied/true ve Rejected/false→Applied/true; C: Received/false→Applied/true ve Rejected/false→Applied/true; F: Received/false→Rejected/false ve Rejected/false→Rejected/false; D: Received/false→Received/false ve Applied/false→Received/false. Özellikler: (P1) çatışma yaratmayan ingest durumu düşürmez; (P2) durum düşüşü yalnız çatışan kimlik kümesine en az bir YENİ `canonical_event_id` girdiği ingest adımında olur (ilk false→true geçişi dahil ama onunla sınırlı değil; conflict zaten true iken de), her düşüşte `state_rollback` + `conflict`+`needs_user`; küme büyümüyorsa düşüş yok; ayrıca fixture G (x/Received t1 → x/Interview t1 → e1/Received t5 → e2/Rejection t3): Received/false → Applied/true → Received/true → Applied/true, 24 permütasyon aynı nihai Applied/true; (P3) `conflict` ingest ile true→false olmaz; (v) eşleşme güveni düşük → duruma katılmaz, `needs_user`; (vi) Offer olayı hiçbir otomatik gönderim/yanıt tetiklemez; (vii) 41 olaylık gerçek Sheet dökümüne eşdeger **sentetik** fixture (22 Application Received, 19 Rejection sınıf dağılımı) ile çıktı Sheet durumuyla uyumlu (kişisel veri yok).
- Geçiş dönemi: claude.ai takibi sürer, EJS Sheet'e **hiç yazmaz** (r2'deki "onaylı Sheet yazımı" kaldırıldı; yalnız salt-okunur dışa aktarım, 1.8).
**3b (D2 şart):**
- **3b.2 claude.ai takibinin kapatılması (aşama 2):** kesme tarihi `T`, 7 günlük örtüşme penceresinde iki kaynağın sınıf uyum raporu; uyum eşiği (`<EŞİK, kullanıcı belirler>`) sağlanmadan claude.ai kapatılmaz; kapatma kullanıcı eylemi; geri dönüş mümkün (ARCHITECTURE §4). Kabul: örtüşmede aynı Message ID → 1 olay + 2 `source_trace`; çelişki → `conflict`+`needs_user` (güven yüksek olsa da).
- **3b.1** `ImapOutcomeSource`/`GmailOutcomeSource` salt-okunur, kullanıcının kendi OAuth uygulaması, yazılı izin yoksa hiçbir kod yolu e-postaya erişemez; aynı port sözleşme testleri adaptöre uygulanır (adaptör değişince çekirdek testleri değişmez).
- Bağımlılık: 3a → D7, Faz 1 ledger; 3b → D2. Eşleme: **P1-1, P1-2**, P2-2 (P2-4 → 2.10).

## Faz 4 — Kapsam genişletme
Ashby/Workable/Personio, (kullanıcı ayrıca açarsa) LinkedIn/Indeed için ayrı sözleşme-risk kararı, isteğe bağlı AI sıralaması (kota içinde; karar vermez, kota aşımında kurala düşer), log rotasyonu. Kabul: her ATS için Faz 2 adaptör kriterleri. Bağımlılık: Faz 2–3, D4. Eşleme: **P2-3, P2-5**.

## P0/P1/P2 → faz tablosu
| Madde | Faz | Not |
|---|---|---|
| P0-1 Keşif | 1 | Employer Watchlist tabanlı |
| P0-2 Triage | 1 | 1.4/1.5, D1 |
| P0-3 Manifest üretici | 2 | |
| P0-4 Tarayıcı testleri | kapalı | AIW-5 §12, yerel kanıt; exact-head CI okunmadı |
| P1-1 Bildirim | 3a | |
| P1-2 Sonuç takibi | 3a (CSV) / 3b (Gmail) | D7 / D2 |
| P1-3 Tek-komut zincir | 2 | |
| P1-4 QUEUE_EMPTY | 1 | |
| P1-5 SmartRecruiters canlı kabul | 2 | D5 |
| P2-1 İkinci ATS (ADP) | 2/4 | Workday + custom form önce; ADP kararı ayrı |
| P2-2 Tracker TEST | 3a | |
| P2-3 Log rotasyonu | 4 | |
| P2-4 Catch-up | 2 (2.10) | |
| P2-5 AI sıralama | 4 | |

## Kapsam dışı / değişmez
Tüm fazlarda: CAPTCHA/MFA atlatma yok; tahmin yok (çalışma izni, sponsorluk, maaş, hukuki); `runtime.json`/`STOP` yalnız kullanıcıda; fail-closed; LinkedIn/Indeed otomatik toplu çekim kapalı, EURES otomasyon dışı; dış sistem yazımı ve yeni OAuth izni için açık kullanıcı onayı.
