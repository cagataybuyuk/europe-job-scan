> **Güncelleme (21 Kasım 2026):** Kullanıcı kararıyla (Paperclip görevi AIW-6, kart `5c2ade95` "Her yolda doğrudan ben") "bağımsız inceleme" ve "PR başına kullanıcı merge onayı" koşulları kaldırıldı ve `CLAUDE.md` "Değişiklik akışı" buna göre güncellendi (CI yeşilse ajan tüm yollarda birleştirebilir). Aşağıdaki §B–§C'deki "yüksek risk → tam akış / düşük risk → haftalık toplu onay” ayrımı bu yüzden tarihsel taslaktır; risk yol tablosu (§B) yine de bir **risk göstergesi** olarak (ör. ajanın ek inceleme isteme takdiri için) kullanılabilir. `runtime.json`/`STOP` yalnız kullanıcıda kalır.

# STATUS.md şablonu ve risk kademeli PR akışı (TASLAK r2, AIW-11)

**Yön KULLANICI ONAYLI** (AIW-6 yorumları `3795ccd7-f452-427c-9398-76f1a3904b5e` 07:22Z ve `3a64b389-1f39-4e87-a9df-10fb91ca25ce` 07:24Z; kart 0967211d `light_process`); **uygulama ayrı, tam akışlı PR + kullanıcı merge onayı gerektirir (onay merge onayı değildir).** Bu şablon Faz 0'da `docs/`'a girer; ilk resmî `STATUS.md` ROADMAP Faz 0.5.1'de, CLAUDE.md PR'ı merge olduktan sonra oluşur. Bu belge CLAUDE.md "Değişiklik akışı" ve "Kaynak önceliği" kurallarını DEĞİŞTİRİR. Onay gelene kadar **mevcut kural geçerlidir**: her PR için branch→exact-head CI→bağımsız inceleme→kullanıcının açık merge onayı; durum için Issue #79 / Drive 02_Guncel_Durum / Sheet. Uygulanırsa CLAUDE.md ayrı, tam-akışlı PR'da güncellenir (CLAUDE.md değişikliği kendisi yüksek riskli sayılır).

## A. Karar özeti
- Issue #79 checkpoint, Drive 02_Guncel_Durum ve Sheet durumu yerine repo kökünde **tek `STATUS.md`**. Operasyonel gerçek ledger'dadır; STATUS.md ledger'dan ve insandan beslenen **özet**tir, durum için ledger öncedir (çelişirse ledger kazanır, çelişki tarihle yazılır).
- Risk kademeli PR akışı: yüksek riskli yollar → tam akış; düşük riskli yollar → CI yeşil + Codex QA onayı varsa merge onayı **toplu (haftalık)** olabilir.

## B. Yüksek / düşük risk yol tablosu
Sınıflama **yola göre, PR'ın dokunduğu dosya kümesi üzerinden** yapılır; **tek bir yüksek riskli dosya tüm PR'ı yüksek riskli yapar.** Listede olmayan yol = yüksek (varsayılan fail-closed). Liste mevcut repo adlarına (AIW-5/AIW-8: `engine_queue`, `submit_policy`, `policy_application_runtime`) dayanır; tam dosya adları repo'da doğrulanmadı — uygulamada `CODEOWNERS`/betikle glob'lar gerçek ağaça karşı doğrulanmalı.

| Risk | Yol / desen (glob) | Gerekçe |
|---|---|---|
| **YÜKSEK** | `src/ejs/services/*submission*`, `src/ejs/**/submit_policy*` | Gönderim yetkisi, tek-gönderim |
| YÜKSEK | `src/ejs/**/policy_application_runtime*`, `src/ejs/**/policy_engine/**` | Gerçek form doldurma/tıklama |
| YÜKSEK | `src/ejs/**/engine_queue*`, `engine-queue/**` (özellikle `engine-queue/control/**`) | Kuyruk, kill switch, `runtime.json`/`STOP` |
| YÜKSEK | `**/ledger*`, `**/*ledger*.py`, SQLite şema/migrasyon dosyaları, `apply_outcome` | Tek doğruluk kaynağı; durum bozulması |
| YÜKSEK | `**/execution_manifest*`, `**/cv_asset_gate*`, `**/ats_adapter_registry*`, tüm ATS **adaptörleri** (`**/adapters/**`, `*smartrecruiters*`, Workday/Greenhouse/Lever) | Neye/nasıl başvurulduğu |
| YÜKSEK | Yanıt bankası okuyucu/yazıcı, `**/answers*`, `rules/answer_bank*`, `rules/auto_apply*`, `rules/cv_selection*` | Tahmin yasağı, yanlış başvuru riski |
| YÜKSEK | `.github/workflows/**`, CI yapılandırması, `CODEOWNERS`, bu risk tablosunun kendisi, `CLAUDE.md`, `INVARIANTS.md` | Güvenlik kapılarını gevşetebilir |
| YÜKSEK | `launchagent/**`, plist üretici, zincir giriş noktası (`run-chain`) | Neyin ne zaman çalıştığı |
| YÜKSEK | Secret/OAuth/e-posta erişimi: `outcome` Gmail/IMAP adaptörleri | Yeni izin/dış erişim |
| YÜKSEK | Bağımlılık dosyaları (`pyproject.toml`, `requirements*`, lock) | Tedarik zinciri; kurulum kararı |
| **DÜŞÜK** | `src/ejs/discover/**`, keşif kaynak adaptörleri (salt-okuma, gönderim kod yolu yok) | Gönderimi etkilemez; çıktı yalnız ledger `discovered` |
| DÜŞÜK | `src/ejs/triage/**` **yalnız etiket üretiyorsa** (karar etiketi gönderimi doğrudan tetiklemiyorsa), `rules/scoring*`, `rules/criteria.schema*` | Etiket; ama `auto_apply` eşiği YÜKSEK (yukarıda) |
| DÜŞÜK | `docs/**` (CLAUDE.md/INVARIANTS.md hariç), `README.md`, `STATUS.md`, arşiv taşımaları | Belge |
| DÜŞÜK | `notifier/**`, `export/**` (salt-okunur çıktı), testler yalnız mevcut kuralı sıkılaştırıyorsa | Gönderimi etkilemez |

Sınır durumlar: (1) triage değişikliği `auto_apply` kararının eşik/sert engel mantığına dokunuyorsa YÜKSEK. (2) Testi **silen/gevşeten** değişiklik YÜKSEK. (3) Sınıf belirsizse YÜKSEK. (4) PR yazarı sınıfı kendisi seçmez; CI betiği değişen dosyalardan hesaplar ve PR'a etiket basar; yazar etiketi düşüremez.

## C. Akış
| | YÜKSEK risk (mevcut tam akış korunur) | DÜŞÜK risk (ÖNERİ) |
|---|---|---|
| Adımlar | branch→PR→exact-head CI→bağımsız inceleme (Codex QA)→düzeltme→**kullanıcının açık "merge" onayı (PR başına)**→squash (head SHA sabit)→main CI | branch→PR→exact-head CI yeşil→Codex QA **onayı** |
| Merge onayı | Tek tek, açık | **Toplu, haftalık**: kullanıcı STATUS.md "Birleştirme bekleyenler" listesini tek mesajla onaylar; yalnız listedeki PR'lar, onay anındaki head SHA ile |
| Koşul | — | CI yeşil (exact-head) **ve** Codex QA onayı **ve** PR etiketi `risk:low` (CI hesaplı). Biri eksikse yüksek akışa düşer |
| Geri alma | Revert PR | Revert PR; haftalık toplu içinde bir PR sonradan sorunlu çıkarsa kalan düşük risk toplu onay **askıya alınır**, tüm PR'lar tek tek onaya döner |
Değişmeyenler: `main`'e doğrudan push yok; dış yazma/yeni OAuth/ücretli kullanım için açık onay; ajan kendi işini onaylamaz (yazar ≠ QA); toplu onay **yüksek riskli yollar için asla** kullanılamaz; kullanıcı toplu onayı istediği an iptal edebilir.

## D. STATUS.md şablonu (repo kökü; kişisel veri/secret yok — repo PUBLIC)
```markdown
# EJS STATUS
Güncelleme: <YYYY-AA-GG SS:DD> — yazan: <kullanıcı|ajan adı>   Ledger anlık görüntüsü: <tarih>
Kaynak önceliği: ledger > bu dosya > tarihsel raporlar. Çelişki silinmez, tarihle açıklanır.

## 1. Şu an (tek satır)
<ör. Faz 1 — keşif geliştirme; gönderim KAPALI (R0)>

## 2. Çalışma durumu (ledger'dan; sayı + zaman)
| Gösterge | Değer | Zaman |
|---|---|---|
| Rampa kademesi | R0 / R1 / R2 / R3 | |
| Gönderim anahtarı (`runtime.json`) | <açık|kapalı — yalnız kullanıcı değiştirir> | |
| Son zincir turu / başarı | | |
| Son keşif (besleyici sağlığı) | ok / degraded / feeder_dead | |
| Toplam başvuru / kalibrasyon sayacı (hedef 30) | | |
| `needs_user` bekleyen | | |
| Mac catch-up (son çalışma X saat önce) | | |

## 3. Aktif iş (faz → kalem → PR → durum)
| Kalem | PR | Risk (yüksek/düşük) | CI (exact-head SHA) | Codex QA | Durum |
|---|---|---|---|---|---|

## 4. Birleştirme bekleyenler
**Yüksek risk (tek tek açık onay gerekir):** <PR listesi, head SHA>
**Düşük risk — haftalık toplu onay listesi (CI yeşil + QA onaylı):** <PR listesi, head SHA, QA onay bağlantısı>

## 5. Kullanıcıdan beklenenler (toplu; günde ≤1 özet)
- [ ] <karar/onay/profil eksiği>  (sahip: kullanıcı; son tarih: <varsa>)

## 6. Engeller ve riskler
| Engel | Sahip | Açılış | Not |
|---|---|---|---|

## 7. Son kararlar (tarihli, değiştirilemez günlük; yeni satır ekle)
| Tarih | Karar | Kaynak (kart/PR) |
|---|---|---|

## 8. Doğrulanmayanlar
<ör. "SmartRecruiters canlı kabulü doğrulanmadı", "Merged ≠ canlı çalıştı">

## 9. Oturum devri (bir sonraki ajan için, ≤10 satır, teknik durum kodu/execution_id; kişisel veri yok)
```
Kurallar: STATUS.md **düşük riskli** yoldur (belge), ama §2'deki sayılar elle yazılmaz, ledger'dan üretilen betikle (ÖNERİ, ayrı iş) yenilenir; "Merged ≠ canlı çalıştı" dili geçerlidir (durum dili: Tests PASS ≠ başvuru yapıldı). Eski kaynakların akıbeti: Issue #79 → kapatılır/"STATUS.md'ye taşındı" notu; Drive 02_Guncel_Durum → salt arşiv; Sheet durumu → salt-okunur dışa aktarım (ARCHITECTURE §5). Geçişte bir süre (öneri 2 hafta) STATUS.md ile Issue #79 yan yana tutulur, çelişkide STATUS.md + ledger kazanır.

## E. Doğrulanmadı
Yol glob'ları gerçek repo ağacına karşı çalıştırılmadı (repo bu görevde değiştirilmedi/taranmadı); CI betiğiyle etiketleme henüz yok (ayrı, yüksek riskli iş: `.github/workflows`). Haftalık ritim/gün ve toplu onay biçimi kullanıcı kararıdır.
