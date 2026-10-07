#!/usr/bin/env bash
set -euo pipefail

install_root="/opt/rasad10"

curl -fsS http://127.0.0.1:18010/api/v1/health >/dev/null
curl -fsSL http://127.0.0.1:14173/ >/dev/null
curl -fsSL http://127.0.0.1:18011/ >/dev/null

cd "${install_root}"
celery_ping=$(docker compose \
  -f docker/compose.server.yml \
  --env-file .env \
  --profile worker exec -T worker \
  celery -A app.workers.celery_app:celery_app inspect ping --timeout 10)
grep -q pong <<<"${celery_ping}"
echo "celery=pong"

docker compose \
  -f docker/compose.server.yml \
  --env-file .env \
  --profile worker exec -T worker python - <<'PY'
import urllib.error
import urllib.request

proxy_url = "http://crawler-egress:8080"
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
)

for name, url, required in (
    ("irna", "https://www.irna.ir", True),
    ("bbc", "https://www.bbc.com/persian", False),
):
    try:
        status = opener.open(url, timeout=30).status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except urllib.error.URLError as exc:
        if required:
            raise
        print(f"egress_{name}=blocked_upstream reason={exc.reason!s}")
        continue
    assert 200 <= status < 500, (name, status)
    print(f"egress_{name}={status}")

try:
    opener.open("http://127.0.0.1", timeout=10)
except urllib.error.HTTPError as exc:
    assert exc.code == 403, exc.code
    print("ssrf_loopback=blocked")
else:
    raise AssertionError("Loopback request unexpectedly passed the egress gateway")
PY

test "$(systemctl is-active nginx)" = "active"
test "$(ss -lntH 'sport = :8081' | wc -l)" -gt 0

for url in https://teb.aifluxa.ir https://tgt.haminyar.ir; do
  status=$(curl -kLsS -o /dev/null -w '%{http_code}' "${url}")
  test "${status}" -ge 200
  test "${status}" -lt 500
  printf 'existing_service=%s status=%s\n' "${url}" "${status}"
done

echo "server_validation_passed"
