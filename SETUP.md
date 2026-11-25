# Meta kurulumu — adım adım

Botun kodu hazır ve testli; eksik olan tek şey Meta tarafı. Aşağıdaki adımlardan
**senin tıklamaların** olanlar 🧑 ile, **benim yapacaklarım** 🤖 ile işaretli.

Hangi yol: **Instagram API with Unified Login** (Meta'nın önerdiği). Üç yol var,
farkları kurulum yükünde:

| Kurulum | Facebook sayfası | `mentions` webhook'u | İzin |
|---|---|---|---|
| **Unified Login** ← seçtiğimiz | gerekmez | ayrı alan | `instagram_manage_comments` |
| Instagram Login | gerekmez | `comments` bildirimine gömülü | `instagram_business_basic` + `instagram_business_manage_comments` |
| Facebook Login | **gerekir** | ayrı alan | `instagram_basic`, `instagram_manage_comments`, `pages_read_engagement`, `pages_show_list` |

Kod üçünde de çalışır: gönderici iki yanıt endpoint'ini de deniyor, API host'u
`GRAPH_BASE_URL` ile değişiyor (`graph.facebook.com` ↔ `graph.instagram.com`).
Doğru host'u tahmin etmeye çalışma — `manage.py check_setup` token'la bir çağrı
yapıp söylüyor; yanlışsa tek satır `.env` değişikliği.

Meta arayüzü sık değişiyor; aşağıdaki menü yolları değişmiş olabilir, parantez
içinde **ne aradığını** yazdım.

---

## Adım 1 🧑 — `@remindme.in` professional hesaba geçsin

1. Instagram uygulaması → profil → ☰ → **Settings and privacy** → **Account type
   and tools** → **Switch to professional account** → kategori seç → **Business**
   (Creator da olur, API açısından fark yok). Geri dönüşü var.
2. Facebook sayfası **gerekmiyor** — yalnızca Facebook Login kurulumunu seçersen
   gerekir. Unified Login'de hesap doğrudan uygulamaya bağlanıyor.

Professional hesap gizli (private) olamaz; bot için zaten istenen bu.

`nudgewire0` için bir şey yapmana gerek yok — yorumu yazacak hesabın professional
olması gerekmiyor.

## Adım 2 🧑 — Meta uygulaması

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) → **Create App**
   → use case olarak **Other** → app type **Business**.
2. Instagram use case'i: yeni panelde ayrı bir "ürün" menüsü yok, her şey
   **Use cases** altında. Dashboard'daki *Customize the **Manage messaging &
   content on Instagram** use case* satırına tıkla — izinler, Instagram hesabı
   bağlama/token üretme ve webhook ayarları hep bu sayfanın içinde.
3. **Facebook Login for Business'a dokunma.** Bu botun tarayıcıdan giriş yapan
   kullanıcısı yok; OAuth redirect URI, Quickstart platformu, deauthorize URL —
   hiçbiri gerekmiyor. (Yanlışlıkla eklendiyse yapılandırmadan bırakmak yeterli.)
4. **App settings → Basic → App secret → Show** → `IG_APP_SECRET`.

## Adım 3 🧑🤖 — Public HTTPS adresi: `remindme.nudgewire.com`

Karar verilen adres bu. Kurulumun tamamı `deploy/README.md`'de adım adım duruyor:
DNS A kaydı → nginx server bloğu → certbot → iki systemd servisi.

Webhook callback URL'i: **`https://remindme.nudgewire.com/webhooks/instagram/`**

DNS ve `sudo` gerektiren adımlar sende; dosyalar (`deploy/nginx-remindme.conf`,
`deploy/remindme-web.service`, `deploy/remindme-worker.service`) hazır.

## Adım 4 🧑 — Webhook yapılandırması

**Use cases → Manage messaging & content on Instagram → Customize** →
Webhooks bölümünde **Configure**:

| Alan | Değer |
|---|---|
| Callback URL | `https://remindme.nudgewire.com/webhooks/instagram/` (sonundaki `/` dahil) |
| Verify Token | `.env` dosyasındaki `IG_VERIFY_TOKEN` değeri — hazır üretildi |

**Verify and Save**'e basmadan önce `remindme-web` servisi ayakta olmalı; Meta bir GET
isteği atıp challenge bekliyor, bizim endpoint onu aynen döndürüyor (testli).

Sonra alan aboneliği: **`comments`** ve **`mentions`** → Subscribe.

## Adım 5 🧑 — Access token ve IG user id

Graph API Explorer'a gerek yok, token doğrudan panelden alınıyor:

1. **Use cases → Manage messaging & content on Instagram → Customize** →
   *Instagram business login* / API setup bölümü.
2. **Add an Instagram account** → `@remindme.in` ile giriş yap ve izin ver.
   (Hesap public olmalı; professional hesaplar zaten public.)
3. Aynı panel iki değeri gösterir:
   - **Instagram user ID** (`17841...`) → `IG_USER_ID`
   - **Generate access token** → `IG_ACCESS_TOKEN`

Bu kurulumda API host'u büyük olasılıkla `graph.instagram.com`:

```
GRAPH_BASE_URL=https://graph.instagram.com
```

Emin olmaya çalışma — `manage.py check_setup` token'la bir çağrı yapıp söylüyor,
yanlışsa tek satırlık `.env` değişikliği.

Bu adım sunucudan bağımsız: Pi hazır olmadan da token alınıp doğrulanabilir.

## Adım 6 🧑🤖 — İlk test: kendi postumuz

App Review'dan önce uygulama **Development mode**'da; webhook'lar yalnızca
uygulamada rolü olan kullanıcılar için güvenilir şekilde çalışıyor. Bu yüzden ilk
testi `@remindme.in`'in **kendi postunun** altında yapmak en kesin yol: o yorum
`comments` alanından gelir, üçüncü taraf izni gerektirmez ve `from.username` de
geldiği için kişi başı limit bile çalışır.

1. `@remindme.in` hesabından herhangi bir gönderi paylaş.
2. `nudgewire0` ile o postun altına: `@remindme.in 1 hour`
3. Ben izlerim: InboxEvent → Reminder → onay yanıtı → 1 saat sonra hatırlatma.

## Erişim seviyesi: Standard mı Advanced mı (maliyeti belirleyen soru)

Meta'nın iki erişim seviyesi var ve aradaki fark bu projede kritik:

| Seviye | Ne zaman gerekir | Bedeli |
|---|---|---|
| **Standard** | Uygulamayı yetkilendiren hesap senin (uygulamada rolü olan hesaplar) | App Review yok, işletme doğrulaması yok |
| **Advanced** | Uygulama, sahibi/yöneticisi olmadığın Instagram professional hesaplarına hizmet ediyorsa | App Review **ve** Business Verification (işletme belgeleri) |

Bizim durumumuz sınırda ve **ampirik olarak test edilmeli**: uygulamaya izin veren
tek hesap `@remindme.in` — o da senin ve uygulamada rolü olacak. Botu etiketleyen
yabancılar "app user" değil, hiçbir şey yetkilendirmiyorlar. Bu okumaya göre
**Standard yeterli olabilir**. Kesin cevabı şu test verir:

1. Uygulamayı Live'a al (Standard Access ile).
2. Uygulamada **hiçbir rolü olmayan** bir hesaptan (ör. `nudgewire0`) başka
   birinin postunda botu etiketle.
3. Webhook geldiyse → Standard yeterli, App Review'a gerek yok.
   Gelmediyse → Advanced gerekiyor, App Review + Business Verification şart.

Advanced gerekirse: uygulamanın doğrulanmış bir **business portfolio**'ya bağlı
olması gerekiyor; doğrulama şirket belgesi (vergi kaydı vb.) istiyor — Türkiye'de
şahıs şirketi genelde yetiyor, hiç kaydı olmayan bir birey için zor.
İşe yarar bir ayrıntı: arayüzü olmayan (headless) uygulamalar için Meta
"private app" istisnası tanıyor ve bu istisnada yalnızca `instagram_basic` ile
`instagram_manage_comments` onayı istenebiliyor — bizim ihtiyacımız tam olarak bu.

## Adım 7 🧑 — Başkasının postunda test (asıl senaryo)

Üçüncü bir hesabın postundaki etiket `mentions` alanından gelir. Development
mode'da gelmezse: uygulama panelinde **App roles → Roles → Add people →
Testers** ile `nudgewire0`'ı ekle, o hesaptan daveti kabul et. Yine gelmiyorsa
sebep App Review eksikliği — `instagram_manage_comments` için Advanced Access
gerekir ve o başvuruyu yapmamız gerekir (gizlilik politikası sayfası + veri silme
sayfası + ekran kaydı).

## Adım 8 🤖 — Gerçek gönderime geç

`.env` içinde `DRY_RUN=false` yapılır, sonra iki süreç:

```bash
.venv/bin/python manage.py runserver 0.0.0.0:8000   # webhook
.venv/bin/python manage.py run_bot                  # kuyruklar
```

---

## Doğrulama komutları

| Komut | Ne yapar |
|---|---|
| `manage.py check_setup` | .env, token, hesap ve abonelikleri tek tek kontrol eder |
| `manage.py simulate_mention --text "@remindme.in 1 hour" --fire` | Meta olmadan tüm akışı yerel çalıştırır |
| `manage.py subscribe_webhook --show` | Hesabın hangi alanlara abone olduğunu gösterir |
| `curl localhost:8000/healthz` | Kuyruk derinliği |
| `manage.py test bot` | 65 test |
