#!/usr/bin/env bash
# Pull and restart, in the order that cannot leave a half-updated service.
#
#   ./deploy/update.sh
#
# Safe to run when nothing changed: pip, migrate and collectstatic are no-ops.
set -euo pipefail

cd "$(dirname "$0")/.."
PY=.venv/bin/python
PIP=.venv/bin/pip

echo "--> git pull"
git pull --ff-only

echo "--> dependencies"
$PIP install -q -r requirements.txt

echo "--> migrations"
$PY manage.py migrate --noinput

echo "--> static files"
$PY manage.py collectstatic --noinput >/dev/null

# Unit files live in the repo; re-read them only when they actually changed.
if ! diff -q deploy/remindme-web.service /etc/systemd/system/remindme-web.service >/dev/null 2>&1 \
   || ! diff -q deploy/remindme-worker.service /etc/systemd/system/remindme-worker.service >/dev/null 2>&1; then
    echo "--> unit files changed, reinstalling"
    sudo cp deploy/remindme-*.service /etc/systemd/system/
    sudo systemctl daemon-reload
fi

echo "--> restart"
# The worker traps SIGTERM and finishes its current pass, so no reply is cut in half.
sudo systemctl restart remindme-web remindme-worker

echo "--> health"
sleep 2
$PY manage.py check_setup || true
curl -s -o /dev/null -w "healthz: %{http_code}\n" https://remindme.nudgewire.com/healthz || true
