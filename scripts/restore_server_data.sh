#!/usr/bin/env bash
set -euo pipefail

install_root="/opt/rasad10"
transfer_root="/opt/rasad10-transfer"

cd "${install_root}"
docker compose \
  -f docker/compose.server.yml \
  --env-file .env up -d postgres rasad-redis
for _ in $(seq 1 90); do
  if docker exec rasad10-postgres-1 \
    pg_isready -U rasad -d rasad >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker exec rasad10-postgres-1 pg_isready -U rasad -d rasad
cat "${transfer_root}/rasad10.dump" | \
  docker exec -i rasad10-postgres-1 \
  pg_restore -U rasad -d rasad --clean --if-exists --no-owner

app_tables=$(docker exec rasad10-postgres-1 psql -U rasad -d rasad -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
test "${app_tables}" -gt 0

printf 'app_tables=%s\n' "${app_tables}"
echo "server_data_restored"
