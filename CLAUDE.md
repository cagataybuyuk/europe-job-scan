# Europe Job Scan (EJS) — Claude Code kuralları

Bu dosya, bu repoda çalışan her Claude Code oturumu için geçerlidir. Kullanıcıyla Türkçe konuş; kod, commit, PR ve doküman metinleri İngilizce kalabilir.

## Ortamlar ve geçiş

EJS iki Claude ortamında yürür; ikisi de aynı kaynakları ve aynı kuralları kullanır:

- **claude.ai Projesi "EJS — Europe Job Scan"**: yönetişim, Drive / Sheets / Gmail işleri, zamanlanmış "Europe Job Scan Daily" görevi.
- **Claude Code (Mac'te yerel)**: geliştirme, test, PR, motorun canlı denemesi ve hata ayıklaması.

Kullanıcı istediği an iki ortam arasında geçebilir. Ortamların sohbet geçmişi birbirine taşınmaz; devir yalnız aşağıdaki kayıtlarla olur. Bu yüzden:

- **Oturum başında**: `main` SHA'sını, açık PR'ları, exact-head CI sonucunu, Issue #79 ve #73'teki son checkpoint'i oku. Drive veya tracker erişimin yoksa bunu söyle.
- **Oturum sonunda veya ortam değişmeden önce**: Issue #79'a checkpoint yorumu yaz (ne birleşti, SHA, CI, açık kalan iş, sıradaki adım). Drive veya Sheets araçların yoksa, gereken 02_Guncel_Durum / tracker güncellemelerini checkpoint'te "claude.ai oturumuna bırakıldı" diye açıkça listele.

## Kaynak önceliği

- Kod: bu repo, `main`. Operasyon: EU Job Tracker Sheet. Yönetişim: Drive 00_BASLA_BURADAN / 01_Proje_Plani / 02_Guncel_Durum ve onaylı GD dokümanları (güncel: GD-006 + Değişiklik A1).
- Çelişkide: en son işveren kaydı veya yerel ledger + canlı tracker okuması > Issue #79 checkpoint > `main` dokümanları > tarihsel raporlar. Çelişkiyi silme; tarihle açıkla.

## Repo PUBLIC

- CV, aday manifesti, form verisi, kişisel bilgi, secret, token veya cookie commit'e, issue'ya ya da PR yorumuna girmez.
- Özel girdiler yalnız repo dışındaki owner-only klasörlerde durur: `~/Local/Projeler/EJS` (`.ejs-local/`, `claude-applications/`, `engine-queue/`).
- Motor sonuç dosyalarından checkpoint'e yalnız durum kodu, execution_id ve teknik bulgu yazılır; aday yanıtları ve form içeriği yazılmaz.

## Değişiklik akışı (yönetim yöntemi)

- `main`'e doğrudan push yok. Her değişiklik: branch → PR → exact-head CI → işi görmemiş ayrı bir alt-ajanla bağımsız inceleme → bulguların düzeltilmesi → **kullanıcının açık "merge" onayı** → squash merge (head SHA sabitlenerek) → birleşme sonrası `main` CI.
- Başlamış bir PR bitmeden aynı sorunu çözen ikinci PR açma; önce açık PR'ları kontrol et.
- Yeni test dosyası `.github/workflows/ci.yml` içindeki ilgili pytest listesine eklenmeli; tarayıcı testleri `upload-e2e-regression` job'unda koşar.
- Her merge sonrası dokümantasyon: Issue #79 checkpoint; Drive 02_Guncel_Durum sürüm artışı; gerekiyorsa GD dokümanına değişiklik bölümü; claude.ai proje dokümanı `EJS_CLAUDE_CALISMA_MODELI.md`.
- Dış sistemlere yazma (tracker, Drive, e-posta, zamanlanmış görev), dış iletişim ve maddi işlem için kullanıcının açık onayı gerekir.

## Durum dili

- Merged ≠ canlı çalıştı. Tests PASS ≠ başvuru yapıldı. Form dolduruldu / CV yüklendi ≠ gönderildi. Submit tıklandı ≠ Applied.
- Applied yalnız doğru işveren ve rol için teyit kanıtıyla yazılır; sonradan Rejected gelse de tarihsel Applied kaydı kalır.
- Plan ile gerçekleşeni ayır; emin olmadığını tarihi ve belirsizliğiyle söyle.

## Güvenlik sınırları

- CAPTCHA, MFA, login veya doğrulama atlatma yok; motor bekler (Awaiting User), kullanıcı çözer. Şifre girme ve hesap açma kullanıcıya aittir.
- Çalışma izni, sponsorluk, maaş, iş geçmişi, demografik ve hukuki yanıt tahmin edilmez; bilinmeyen bilinmeyen kalır.
- Pazarlama ve talent pool izinleri varsayılan olarak Hayır veya boş.
- Form şeması, host, seçenekler, CV dosyası veya başarı yanıtı beklenenden farklıysa dur. Belirsiz kalan ya da çöken bir gönderimi yeniden deneme.
- Canlı gönderim yalnız SUBMIT-1.0 politikası içinde (R4: günlük sınır yok).
- `engine-queue/control/` (`runtime.json`, `STOP`) yalnız kullanıcı tarafından yazılır; motoru açmak veya kapatmak kullanıcının işidir.

## Yerel düzen (Mac)

- Repo `~/europe-job-scan`, sanal ortam `.venv`.
- Motor: `python -m ejs.apps.policy_engine` (LaunchAgent `com.ejs.policy-engine`, her gün 08:30). Kuyruk ve günlük: `~/Local/Projeler/EJS/engine-queue/` (`logs/engine.log`). Ayrıntı: `docs/ENGINE_QUEUE.md`, `docs/EXECUTION_MANIFEST.md`, `docs/SMARTRECRUITERS_POLICY_BROWSER.md`, `docs/POLICY_SUBMISSION.md`.

## Test

- `python -m pip install -e '.[test]' && python -m playwright install chromium && python -m pytest -q`
- Yönetilen Chromium yoksa tarayıcı testleri için `EJS_TEST_CHROME=<chrome yolu>` kullan.
