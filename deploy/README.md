# Raspberry Pi'ye kurulum — remindme.nudgewire.com

Webhook adresi (Meta formuna yazılacak):
**`https://remindme.nudgewire.com/webhooks/instagram/`**

## 1. DNS

`remindme` için bir **A kaydı** → Pi'nin public IP'si (veya `nudgewire.com`'un
bulunduğu host'a **CNAME**). Cloudflare kullanıyorsan proxy açık kalabilir,
SSL modu **Full (strict)** olsun — webhook POST'ları sorunsuz geçer.

```bash
dig +short remindme.nudgewire.com      # yayıldı mı
```

## 2. PostgreSQL

```bash
sudo apt update && sudo apt install -y postgresql

DB_PASS=$(openssl rand -hex 24)        # URL'de kaçış gerektirmeyen bir parola
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE remindme LOGIN PASSWORD '${DB_PASS}';
ALTER ROLE remindme CREATEDB;                                     -- manage.py test için
ALTER ROLE remindme SET client_encoding TO 'utf8';
ALTER ROLE remindme SET default_transaction_isolation TO 'read committed';
ALTER ROLE remindme SET timezone TO 'UTC';
CREATE DATABASE remindme OWNER remindme;
SQL

echo "POSTGRES_CONNECTION_STRING=postgres://remindme:${DB_PASS}@127.0.0.1:5432/remindme"
```

Son satır `.env` dosyasına yazılacak bağlantı dizesini basar. Doğrula:

```bash
PGPASSWORD='<parola>' psql -h 127.0.0.1 -U remindme -d remindme -c '\conninfo'
```

Veritabanı yalnızca `127.0.0.1` üzerinden erişilebilir; Debian'ın varsayılan
`pg_hba.conf`'u bu bağlantı için parola (scram-sha-256) ister, dışarıya kapalıdır.

## 3. Kod ve sanal ortam

```bash
git clone git@github.com:mutcato/remind-me.git ~/apps/remind-me
cd ~/apps/remind-me
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt gunicorn
cp .env.example .env            # SETUP.md Adım 5'teki değerleri + POSTGRES_CONNECTION_STRING
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser    # admin'e girmek için
```

`psycopg` için hazır tekerlek bulunamazsa (nadir): `sudo apt install -y libpq-dev python3-dev`
sonra tekrar dene.

`.env` içinde bu üç satır doğru olmalı:

```
DJANGO_ALLOWED_HOSTS=remindme.nudgewire.com,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://remindme.nudgewire.com
POSTGRES_CONNECTION_STRING=postgres://remindme:<parola>@127.0.0.1:5432/remindme
```

## 4. nginx + TLS

```bash
sudo cp deploy/nginx-remindme.conf /etc/nginx/sites-available/remindme
sudo ln -s /etc/nginx/sites-available/remindme /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d remindme.nudgewire.com
```

## 5. İki servis

```bash
sudo cp deploy/remindme-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now remindme-web remindme-worker
sudo systemctl status remindme-web remindme-worker
journalctl -u remindme-worker -f          # worker'ı canlı izle
```

## 6. Güncelleme

Kod değiştiğinde:

```bash
./deploy/update.sh      # pull -> pip -> migrate -> collectstatic -> restart -> health
```

Elle yapacaksan sıra önemli: `git pull` çalışan süreci etkilemez, kodu yalnızca
yeniden başlatma yükler. `daemon-reload` sadece `deploy/*.service` dosyaları
değiştiyse gerekir.

```bash
sudo systemctl restart remindme-web remindme-worker
```

## 7. IPv6 tuzağı

`graph.facebook.com`'un hem A hem AAAA kaydı var. IPv6 rotası olmayan bir
bağlantıda giden çağrılar aralıklı olarak `[Errno 101] Network is unreachable`
verir — gelen webhook çalışırken giden API çağrıları düşer, kafa karıştırıcıdır.

Önce teşhis:

```bash
curl -4 -s -o /dev/null -w "IPv4: %{http_code}\n" https://graph.facebook.com/v27.0/
curl -6 -s -o /dev/null -w "IPv6: %{http_code}\n" https://graph.facebook.com/v27.0/
ip -6 route | head -3        # boşsa IPv6 rotası yok demektir
```

İki çözümden biri:

```bash
# a) sistem geneli: IPv4'ü tercih et (root gerekir, tüm uygulamaları etkiler)
echo 'precedence ::ffff:0:0/96  100' | sudo tee -a /etc/gai.conf
getent ahosts graph.facebook.com | head -2      # artık IPv4 başta olmalı

# b) yalnızca bu uygulama: .env içinde
FORCE_IPV4=true
```

## 8. Doğrula

```bash
curl -s https://remindme.nudgewire.com/healthz          # kuyruk derinliği
.venv/bin/python manage.py check_setup                  # token + abonelikler
```

Webhook doğrulama testi (Meta'nın yaptığı GET isteğinin aynısı — `<verify-token>`
yerine `.env`'deki `IG_VERIFY_TOKEN`):

```bash
curl -s "https://remindme.nudgewire.com/webhooks/instagram/?hub.mode=subscribe&hub.verify_token=<verify-token>&hub.challenge=12345"
# beklenen çıktı: 12345
```

Bu `12345` dönüyorsa Meta panelindeki **Verify and Save** de geçer.
