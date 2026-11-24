# Meta kurulumu — adım adım

Botun kodu hazır ve testli; eksik olan tek şey Meta tarafı. Aşağıdaki adımlardan
**senin tıklamaların** olanlar 🧑 ile, **benim yapacaklarım** 🤖 ile işaretli.

Hangi yol: **Instagram API with Facebook Login**. Sebebi: bizim akış birebir bu
yola uyuyor — başkalarının postundaki etiketler için ayrı bir `mentions` webhook
alanı ve yanıt için `POST /{ig-user-id}/mentions`. Gereği: Instagram hesabının bir
Facebook sayfasına bağlı olması. (Instagram Login yolu da çalışır — `mentions`
orada `comments` bildiriminin içinde gelir, gönderici de iki endpoint'i deneyecek
şekilde yazıldı — ama o yolda `GRAPH_BASE_URL=https://graph.instagram.com` olmalı.)

Meta arayüzü sık değişiyor; aşağıdaki menü yolları değişmiş olabilir, parantez
içinde **ne aradığını** yazdım.

---

## Adım 1 🧑 — `@remindme.in` professional hesap + Facebook sayfası

1. Instagram uygulaması → profil → ☰ → **Settings and privacy** → **Account type
   and tools** → **Switch to professional account** → kategori seç → **Business**.
2. Hesabı bir Facebook sayfasına bağla (aradığın şey: Instagram hesabının bir
   Page'e bağlı olması). En güvenilir yol Facebook tarafından: ilgili **Page →
   Settings → Linked accounts → Instagram → Connect account**.
   Sayfan yoksa boş bir sayfa aç, içerik gerekmiyor.

`nudgewire0` için bir şey yapmana gerek yok — yorumu yazacak hesabın professional
olması gerekmiyor.

## Adım 2 🧑 — Meta uygulaması

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) → **Create App**.
2. Use case listesinde Instagram ile ilgili olanı seç (aradığın şey: uygulamaya
   **Instagram** ürününün eklenebilmesi); app type sorarsa **Business**.
3. Uygulama panelinde **Products → Instagram → Set up**.
4. **App settings → Basic → App secret → Show**. Bu değer bana gelecek
   (`IG_APP_SECRET`) — webhook imzasını bununla doğruluyoruz.

## Adım 3 🧑🤖 — Public HTTPS adresi: `remindme.nudgewire.com`

Karar verilen adres bu. Kurulumun tamamı `deploy/README.md`'de adım adım duruyor:
DNS A kaydı → nginx server bloğu → certbot → iki systemd servisi.

Webhook callback URL'i: **`https://remindme.nudgewire.com/webhooks/instagram/`**

DNS ve `sudo` gerektiren adımlar sende; dosyalar (`deploy/nginx-remindme.conf`,
`deploy/remindme-web.service`, `deploy/remindme-worker.service`) hazır.

## Adım 4 🧑 — Webhook yapılandırması

Uygulama panelinde **Instagram → Webhooks** (veya Products → Webhooks → Instagram):

| Alan | Değer |
|---|---|
| Callback URL | `https://remindme.nudgewire.com/webhooks/instagram/` (sonundaki `/` dahil) |
| Verify Token | `.env` dosyasındaki `IG_VERIFY_TOKEN` değeri — hazır üretildi |

**Verify and Save**'e basmadan önce `remindme-web` servisi ayakta olmalı; Meta bir GET
isteği atıp challenge bekliyor, bizim endpoint onu aynen döndürüyor (testli).

Sonra alan aboneliği: **`comments`** ve **`mentions`** → Subscribe.

## Adım 5 🧑 — Access token ve IG user id

[Graph API Explorer](https://developers.facebook.com/tools/explorer) → uygulamanı seç:

1. İzinleri ekle: `instagram_basic`, `instagram_manage_comments`,
   `pages_show_list`, `pages_read_engagement` → **Generate Access Token**.
2. `GET /me/accounts` → sayfanın `id`'si.
3. `GET /{page-id}?fields=instagram_business_account` → dönen `id`, bizim
   **`IG_USER_ID`**'miz (`17841...` ile başlar).
4. Kısa ömürlü token'ı 60 günlüğe çevir:
   `GET /oauth/access_token?grant_type=fb_exchange_token&client_id={app-id}&client_secret={app-secret}&fb_exchange_token={kısa-token}`

Bana üç şey ver: **app secret**, **IG user id**, **uzun ömürlü token**.
`.env`'e yazar, doğrularım:

```bash
.venv/bin/python manage.py check_setup          # token + hesap + abonelikler
.venv/bin/python manage.py subscribe_webhook --fields comments,mentions
```

## Adım 6 🧑🤖 — İlk test: kendi postumuz

App Review'dan önce uygulama **Development mode**'da; webhook'lar yalnızca
uygulamada rolü olan kullanıcılar için güvenilir şekilde çalışıyor. Bu yüzden ilk
testi `@remindme.in`'in **kendi postunun** altında yapmak en kesin yol: o yorum
`comments` alanından gelir, üçüncü taraf izni gerektirmez ve `from.username` de
geldiği için kişi başı limit bile çalışır.

1. `@remindme.in` hesabından herhangi bir gönderi paylaş.
2. `nudgewire0` ile o postun altına: `@remindme.in 1 hour`
3. Ben izlerim: InboxEvent → Reminder → onay yanıtı → 1 saat sonra hatırlatma.

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
