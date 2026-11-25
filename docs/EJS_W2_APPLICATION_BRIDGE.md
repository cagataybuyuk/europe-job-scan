# EJS-W2 — Keşiften başvuru hazırlığına

Köprü, `EJS-SHADOW-REPORT-2` raporunu okuyup `EJS-APPLICATION-PREPARATION-1`
üretir. Kimlik, ATS ailesi, bölge, tenant ve yayın kimliğinden oluşur.
Aynı kimliğe ait satırlar tek adayda sayılır. Çelişen URL, ayrıntı okumasını
durdurur. Kimliği eksik satırlar ayrı tutulur. SmartRecruiters önce işlenir.
Keşif puanı yalnızca sıralamayı belirler. İşverenin maaş veya sponsorluk
bilgisini belirtmemesi tek başına hazırlığı engellemez.

## Çevrimdışı çalıştırma

Depo kökünden, Python paketinin kurulu olduğu ortamda çalıştırın.
`PRIVATE_PARENT` sahibi tarafından kontrol edilen, Git deposu ve kuyruk
dışında, sembolik bağ içermeyen mutlak bir dizin olmalıdır. Çıktı dizini
`0700`, rapor dosyası `0600` izinleriyle hazırlanır. Yazım tamamlanınca
`preparation-report.json` atomik olarak yayımlanır. Mevcut rapor korunur.
Yayım öncesindeki bir hata sonrasında aynı hedefe yeniden yazılabilir.

```sh
PYTHONPATH=src python scripts/application_bridge.py "$W1_REPORT" "$PRIVATE_PARENT/phase-b"
```

CLI yalnızca yerel dosyaları okur. Sentetik denemede
`--fixtures tests/fixtures/application_bridge/detail.json --detail-budget 1`
kullanılır. Fixture anahtarı, sentetik rapordaki tenant ve yayın kimliğinin
oluşturduğu ayrıntı URL’siyle eşleşmelidir. Test paketi sentetik kaynak raporu
kurar. Servis çağıranın sağladığı transport üzerinden benzersiz ve uyumlu
aday başına bir deneme yapar. Çalışma başına üst sınır 200 deneme, yanıt başına
üst sınır 1.000.000 bayttır. Başarısız deneme bütçeyi tüketir.

## Kimlik ve kanıt

- `target.company` SmartRecruiters slug’ıdır (örneğin `Rituals1`). İnsan
  tarafından görülen işveren adı raporda ayrı kalır. `target.posting_id` URL
  yolundaki sayısal yayın kimliğidir. Kanonik ilan URL’si manifestin
  `apply_url` alanına karşılık gelir. OneClick URL’si ileride görünür başvuru
  eyleminden gözlemlenir.
- `target.requisition_id`, mevcut tekli başvuru denetimindeki gibi,
  aynı tenant ve yayın kimliğine bağlı ayrıntı yanıtının `refNumber`
  alanından gelir. Yayın kimliği, publication UUID ve iş talebi kimliği
  ayrı alanlardır. Ayrıntı kaynağı ve varsa yanıt özeti raporda izlenir.
- `active: true`, ilan etkinliği sinyalidir. Formun açılması, başvuru
  düğmesi ve gönderim uç noktası ayrı gözlem gerektirir. `active: false`
  doğrudan işveren kaynaklı engeldir. Diğer değerler belirsiz kalır.
- Sınırlı İngilizce sponsorluk ve çalışma hakkı ifadeleri kaynak bölümü
  ve alıntıyla kaydedilir. Eşleşme yokluğu, koşul yokluğunun kanıtı sayılmaz.
  Zorunlu nitelikler ve bir koşulun adaya uygulanması ayrıca incelenir.
  Adayın çalışma hakkı veya sponsorluk ihtiyacı ilana bakılarak çıkarılmaz.

| Durum | Anlamı |
| --- | --- |
| `ready_for_form_observation` | Uyumlu ilan kimliği, etkinlik sinyali ve bağlı iş talebi kimliği var. Saptanan ve adayla karşılaştırılması gereken koşul yok. |
| `missing_evidence` | Kimlik, ayrıntı, etkinlik, iş talebi kimliği veya koşulun adaya uygulanması için ek kanıt gerekli. |
| `hard_blocker` | Bağlı işveren ayrıntısı ilanın pasif olduğunu bildiriyor. |
| `unsupported_ats` | Bu dalgada ATS için hazırlık adaptörü bulunmuyor. |

Phase B özeti 414 benzersiz adaydır. 164 Rituals ilanının kanonik URL’si
ve yayın kimliği sözleşmeyle uyumludur. Ayrıntı çekilmediği için bu 164 ilan
`detail_not_observed` durumundadır. 156 Greenhouse ve 94 Lever ilanı
`unsupported_ats` durumundadır. İki çevrimdışı çalıştırma aynı çıktıyı
üretti. Kaynak dosyasının SHA-256 değeri değişmedi.

## Tek bir işveren teyidine giden adım

Ayrı yetkiyle, önce Rituals `743999976290168` (Financial Controller,
Amsterdam) için tek bir salt okunur ayrıntı kontrolü yapılabilir. Bu seçim
işleme sırasındaki ilk SmartRecruiters kaydına dayanır. İşverenden
`company.identifier`, `id`, `refNumber` ve etkinliği doğrulayın.
Ardından zorunlu nitelikleri, çalışma hakkını ve varsa açık sponsorluk
kısıtını doğrulanmış aday gerçekleriyle karşılaştırın. Somut uyumsuzlukta
sıradaki kayıt incelenir. Uygun kayıtta mevcut
`smartrecruiters_live_inspector` ile görünür başvuru girişini ve form
yapısını salt okunur gözlemleyin. Mevcut `observe` manifest çalıştırması
CV yükleyip ilerleyebildiğinden, bu adım için salt okunur denetçiyi kullanın.

Geçerli paket için özel kayıt defterinden doğrulanmış aday gerçekleri ve
mevcut cevap kaynağı kuralları gerekir. Onaylı PDF’nin dosya adı, MIME
türü, boyutu ve SHA-256 değeri onayla eşleşmelidir. Gözlemden form adımları,
kontrol türleri, seçenekler, zorunluluk ve CV alanı alınır.
`form_fingerprint` bu yapıyı bağlar. Kanonik fırsat anahtarı, profil sürümü
ve gerçek `refNumber` ile `submit_key` kurulur. Politika, çalışma ve
tazelik alanları [manifest sözleşmesine](EXECUTION_MANIFEST.md) uyar.
`validate_candidate_manifest` gerçek manifest doğrulayıcısını çağırır
ve paketin hedefini rapor adayına bağlar. Sentetik test geçerli bir paketi,
hedef uyuşmazlığını ve bozuk form parmak izini doğrular.

Bağımsız hedef ve uç nokta incelemesi, yinelenen başvuru denetimi ve ayrı
gönderim yetkisi mevcut motorun kapılarıdır. İşveren teyidi ve kalıcı kayıt
alındığında tek başvuru tamamlanmış sayılır. Hazırlık raporunda
`executable` ve `submit_authorized` değerleri `false` kalır.
