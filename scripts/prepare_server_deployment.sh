#!/usr/bin/env bash
set -euo pipefail

install_root="/opt/rasad10"
transfer_root="/opt/rasad10-transfer"
public_host="5.10.249.72"

tar -xzf "${transfer_root}/project-source-complete.tar.gz" -C "${install_root}"

app_env="${install_root}/.env"
sed -i 's|^ENVIRONMENT=.*|ENVIRONMENT=production|' "${app_env}"
sed -i 's|^FRONTEND_PORT=.*|FRONTEND_PORT=14173|' "${app_env}"
sed -i 's|^API_PORT=.*|API_PORT=18010|' "${app_env}"
sed -i "s|^API_CORS_ORIGINS=.*|API_CORS_ORIGINS=http://${public_host}:14173|" \
  "${app_env}"
if ! grep -q '^AUTH_SECRET_KEY=' "${app_env}"; then
  printf 'AUTH_SECRET_KEY=%s\n' "$(openssl rand -hex 32)" >> "${app_env}"
fi

chmod 600 "${app_env}"
chown -R root:root "${install_root}"

docker compose \
  -f "${install_root}/docker/compose.server.yml" \
  --env-file "${app_env}" --profile worker config --quiet

echo "server_files_prepared"
