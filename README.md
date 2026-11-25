# @remindme.in — Instagram mention bot

Bir Instagram postunun yorumlarında botu etiketleyip süre yazarsın; bot anladığı
tarihi hemen yanıtlar, vakti geldiğinde aynı yorumun altına hatırlatma yazar.

```
@ahmet: @remindme.in 3 gün tarifi dene
Bot:    ⏰ Not aldım: 27 Kasım Cuma 21:57'de buraya tekrar yazacağım. Notun: “tarifi dene”
            │  3 gün sonra
            ▼
Bot:    🔔 Hatırlatma! 3 gün önce buraya dönmek istemiştin. Notun: “tarifi dene”
```

Tasarım kararları ve bu botun neden ilk sırada olduğu: [HANDOFF.md](HANDOFF.md).

## Komut sözdizimi

| Yorum | Sonuç |
|---|---|
| `@remindme.in 3 gün` · `2 hafta` · `90m` · `1 hafta 2 gün` | Şu andan itibaren o kadar sonra (birimler toplanır) |
| `@remindme.in bir saat sonra` · `in 3 days` | Sayı kelimeleri ve İngilizce de çalışır |
| `@remindme.in yarın 09:00` · `cuma akşam` · `friday 6pm` | Belirli gün/saat (akşam=20:00, sabah=09:00, öğlen=12:00) |
| `@remindme.in 24 aralık` | Kuralların kaçırdığını `dateparser` yakalar |
| `@remindme.in 3 gün tarifi dene` | Süre dışında kalan metin **not** olur, yanıtta geri yazılır |
| `@remindme.in aynı` | Aynı posttaki en son hatırlatmanın saatine katılır |

Saat dilimi `Europe/Istanbul`, dil yorumdan algılanır (tr/en). Bot anladığı tarihi
**her zaman** onay yanıtında yazar; yanlış anladıysa kullanıcı anında görür.

## Kurulum

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # değerleri doldur
.venv/bin/python manage.py migrate
.venv/bin/python manage.py test bot     # 63 test
```

### Meta tarafı olmadan deneme

Token, webhook, hatta internet gerekmeden tüm akışı çalıştırır:

```bash
.venv/bin/python manage.py simulate_mention --text "@remindme.in 3 gün tarifi dene" --fire
```

`--fire` hatırlatmanın vaktini şimdiye çeker, böylece hem onay hem hatırlatma
metnini tek seferde görürsün. Hiçbir şey Instagram'a gönderilmez.

## Meta tarafı (kodla paralel yürür)

1. `@remindme.in` hesabını **professional** hesaba çevir.
2. Meta uygulaması aç; **Instagram** ürününü ekle.
3. Webhook: callback URL `https://<alan-adın>/webhooks/instagram/`, verify token
   `.env` içindeki `IG_VERIFY_TOKEN`. Doğrulama GET isteğini bu uygulama karşılar.
4. Abone olunacak alanlar: `mentions` (başkalarının postundaki etiketler) ve
   `comments` (kendi postlarındaki yorumlar).
   ```bash
   .venv/bin/python manage.py subscribe_webhook --fields comments,mentions
   ```
5. İzinler — **App Review** gerekiyor:
   - *Facebook Login* kurulumu: `instagram_basic`, `instagram_manage_comments`,
     `pages_read_engagement`, `pages_show_list`
   - *Instagram Login* kurulumu: `instagram_business_basic`,
     `instagram_business_manage_comments` (bu kurulumda `mentions` ayrı bir alan
     değil, `comments` bildiriminin içinde geliyor)
   - *Unified Login*: `instagram_manage_comments`
6. App Review için gizlilik politikası sayfası, veri silme sayfası ve çalışan
   akışın ekran kaydı gerekiyor. Onay gelene kadar yalnızca uygulamaya eklenmiş
   **test hesaplarıyla** denenebilir — bu arada `DRY_RUN=true` ile çalıştır.

## Çalıştırma

İki süreç: webhook'u karşılayan web sunucusu ve kuyrukları işleyen worker.

```bash
.venv/bin/python manage.py runserver 0.0.0.0:8000     # geliştirme
.venv/bin/gunicorn config.wsgi --bind 0.0.0.0:8000    # üretim (pip install gunicorn)
.venv/bin/python manage.py run_bot                    # worker (sürekli)
.venv/bin/python manage.py run_bot --once             # tek geçiş (cron için)
```

systemd (Raspberry Pi / VPS):

```ini
# /etc/systemd/system/remindme-web.service
[Service]
WorkingDirectory=/home/pi/apps/remind-me
ExecStart=/home/pi/apps/remind-me/.venv/bin/gunicorn config.wsgi --bind 127.0.0.1:8000
Restart=always

# /etc/systemd/system/remindme-worker.service
[Service]
WorkingDirectory=/home/pi/apps/remind-me
ExecStart=/home/pi/apps/remind-me/.venv/bin/python manage.py run_bot
Restart=always
```

Worker'ı sürekli çalıştırmak istemezsen cron da yeter: `* * * * * manage.py run_bot --once`.

Kuyruk derinliği: `GET /healthz`. Kayıtları incelemek/elle düzeltmek için Django
admin (`/admin/`) üç tabloyu da gösterir.

## Mimari

```
POST /webhooks/instagram/   imzayı doğrula (X-Hub-Signature-256) → InboxEvent → 200
                            (webhook isteği içinde başka hiçbir şey yapılmaz)
        │
run_bot │ process_inbox()   mentioned_comment ile metni çek → komutu ayrıştır
        │                   → Reminder (+ onay yanıtı OutboxReply)
        │ schedule_due()    vakti gelen Reminder → hatırlatma OutboxReply
        │ send_outbox()     tek elden, hız sınırlı gönderim → Instagram
        ▼
```

| Tablo | Rolü |
|---|---|
| `InboxEvent` | Gelen webhook olayı. `comment_id` **unique** → Meta tekrar gönderirse yok sayılır |
| `Reminder` | Ayrıştırılmış istek: `due_at`, `note`, `delay_phrase`, `locale`, durum |
| `OutboxReply` | Gönderilecek tek bir yorum. `(reminder, kind)` unique → onay ve hatırlatma birer kez |

- **Yanıt metinleri LLM ile değil şablonlarla** üretilir; her tür için birkaç
  varyasyon var ve varyasyon `comment_id`'den türetilir (aynı yorum hep aynı
  metni alır, farklı yorumlar farklı metin → spam filtresi riski azalır).
- **Tek gönderici**: `send_outbox` istekler arasında `SEND_INTERVAL_SECONDS`
  bekler ve saatlik `SEND_MAX_PER_HOUR` bütçesini aşmaz.
- **Kendi yorumlarımıza asla tepki verilmez** (`from.id == IG_USER_ID`) — yanıt
  döngüsü böyle başlar.
- Geçici hatalar (429, 5xx, kod 4/17/32/613) katlanarak yeniden denenir; kalıcı
  hatalar (yorum silinmiş, yorumlar kapatılmış, token geçersiz) anında
  `failed` işaretlenir.

## API gerçekleri (bu oturumda doğrulandı)

- `mentions` webhook'u yalnızca `media_id` + `comment_id` taşır; metin
  `GET /{ig-user-id}?fields=mentioned_comment.comment_id(<id>){...}` ile çekilir.
- **`mentioned_comment` yorumu yazanın kullanıcı adını döndürmüyor**
  (alanlar: `id`, `text`, `timestamp`, `like_count`, `media`) → HANDOFF §8.1'in
  cevabı: hayır. `@remindme.in` için sorun değil, çünkü bir yorumun altına yanıt
  yazıldığında Instagram yorumun sahibine bildirim gönderiyor. Kullanıcı adı
  yalnızca `comments` webhook'unda (kendi postlarımız) geliyor; kişi başı limit de
  bu yüzden sadece orada uygulanabiliyor.
- Yanıt: `POST /{ig-user-id}/mentions` (`media_id`, `comment_id`, `message`),
  kendi postumuzdaki yorumlar için `POST /{ig-comment-id}/replies`.
- Meta'nın belgelediği sınırlar: **story** etiketleri desteklenmiyor, fotoğrafta
  etiketlenmeye yanıt yazılamıyor, **gizli hesapların** postlarında webhook hiç
  gelmiyor, yorumlar kapatılmışsa `mentioned_comment` hata veriyor.
- Gecikmeli (3 gün sonra) **public** yanıt için belgelenmiş bir zaman penceresi
  yok (7 günlük pencere DM/private reply için). Yine de ilk hafta uçtan uca
  gerçek bir hesapla denenmeli.

## Ampirik bulgular (sahada ölçüldü, belgede yazmıyor)

**25 Kasım 2026 — mention bildirimi takip ilişkisine bağlı görünüyor.**

| Senaryo | Sonuç |
|---|---|
| Takip etmeyen hesaptan yorumda `@remindme.in` etiketi | Bildirim **gelmedi** (birden çok deneme) |
| Aynı hesap `@remindme.in`'i takip ettikten sonra (karşılıklı) | Bildirim **anında geldi** |

Meta'nın belgelerinde bunu açıklayan bir madde yok; resmî metin koşulsuz
(*"When you mention someone, they'll get a notification in their Activity feed"*).
Muhtemelen belgelenmemiş bir spam filtresi.

Aynı denemede doğrulanan ikinci şey: bildirimde handle **`@remindme.in` tam
haliyle, nokta dahil** mavi link olarak göründü. Yani kullanıcı adındaki nokta
mention'ı bölmüyor — bir ara şüphelendiğimiz bu risk kapandı, ad değişmeyecek.

**Henüz bilinmeyen iki şey, ikisi de ürünün şeklini belirliyor:**

1. **Tek yönlü mü, karşılıklı mı?** Testte hesap takip etti *ve* geri takip
   edildi. Tek yönlü takip yetiyorsa akış "önce takip et, sonra etiketle" olur —
   bu normal ve büyümeye yarar. Karşılıklı şartsa bot her kullanıcıyı geri takip
   etmek zorunda kalır; bu hem ölçeklenmez hem spam örüntüsüdür.
2. **Webhook aynı kurala tabi mi?** `mentions` webhook'u yorum oluşturulurken
   sunucu tarafında tetikleniyor; Activity bildiriminden bağımsız olabilir.
   Abonelik açıldığında takipçi/takipçi-olmayan iki hesapla ölçülecek.

## Sessizliğin kaynağını ayırmak

Gerçek bir yorum geldiğinde hiçbir şey olmuyorsa, sorun bizde mi Meta'da mı?

```bash
python manage.py selftest_webhook
```

Meta'nın göndereceğinin birebir aynısı olan imzalı bir teslimatı public callback
URL'ine atar ve kaydın InboxEvent -> Reminder -> OutboxReply yolculuğunu izler.
Instagram'a hiçbir şey gönderilmez. Geçerse yığın sağlamdır ve sessizliğin sebebi
Meta tarafındadır: abonelik ya da Live modu.

## Sırada ne var

- **LLM fallback** (HANDOFF §4): kurallar + `dateparser` anlamazsa şu an
  sessiz kalıyoruz. Bağlanacak yer belli: `bot/timeparse.py` sonundaki
  "3. Nothing understood" dalı. Önce filtre (uzunluk, kişi başı günlük limit)
  sonra çağrı.
- Hesap eşleştirme için `messages` (DM) aboneliği — `@haritama` için şart.
- `@haritama`: `bot/router.py` ikinci bir handle'ı `register()` ile alır; webhook,
  imza, kuyruk ve gönderici aynı kalır.
- Gizlilik politikası + veri silme sayfaları (App Review ve KVKK aynı iş).
