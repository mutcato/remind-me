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

## 2. Kod ve sanal ortam

```bash
git clone git@github.com:mutcato/remind-me.git ~/apps/remind-me
cd ~/apps/remind-me
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt gunicorn
cp .env.example .env            # SETUP.md Adım 5'teki değerleri doldur
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser    # admin'e girmek için
```

`.env` içinde bu iki satır subdomain'e uymalı:

```
DJANGO_ALLOWED_HOSTS=remindme.nudgewire.com,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://remindme.nudgewire.com
```

## 3. nginx + TLS

```bash
sudo cp deploy/nginx-remindme.conf /etc/nginx/sites-available/remindme
sudo ln -s /etc/nginx/sites-available/remindme /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d remindme.nudgewire.com
```

## 4. İki servis

```bash
sudo cp deploy/remindme-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now remindme-web remindme-worker
sudo systemctl status remindme-web remindme-worker
journalctl -u remindme-worker -f          # worker'ı canlı izle
```

## 5. Doğrula

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
