#!/usr/bin/env bash

set -euo pipefail

POSTGRES_VERSION="${POSTGRES_VERSION:-16}"
POSTGRES_LISTEN_ADDRESS="${POSTGRES_LISTEN_ADDRESS:-localhost,192.168.101.6}"
DB_AUTH_METHOD="${DB_AUTH_METHOD:-md5}"

POSTGRES_CONF="/etc/postgresql/${POSTGRES_VERSION}/main/postgresql.conf"
PG_HBA_CONF="/etc/postgresql/${POSTGRES_VERSION}/main/pg_hba.conf"

if [[ ! -f "${POSTGRES_CONF}" ]]; then
    echo "postgresql.conf not found: ${POSTGRES_CONF}" >&2
    echo "Set POSTGRES_VERSION correctly and rerun." >&2
    exit 1
fi

if [[ ! -f "${PG_HBA_CONF}" ]]; then
    echo "pg_hba.conf not found: ${PG_HBA_CONF}" >&2
    echo "Set POSTGRES_VERSION correctly and rerun." >&2
    exit 1
fi

python3 - <<'PY' "${POSTGRES_CONF}" "${POSTGRES_LISTEN_ADDRESS}"
from pathlib import Path
import re
import sys

conf_path = Path(sys.argv[1])
listen_address = sys.argv[2]
text = conf_path.read_text(encoding='utf-8')
replacement = f"listen_addresses = '{listen_address}'"

if re.search(r"^\s*#?\s*listen_addresses\s*=.*$", text, flags=re.MULTILINE):
    text = re.sub(r"^\s*#?\s*listen_addresses\s*=.*$", replacement, text, count=1, flags=re.MULTILINE)
else:
    text += f"\n{replacement}\n"

conf_path.write_text(text, encoding='utf-8')
PY

ensure_pg_hba_entry() {
    local cidr="$1"
    local entry="host    all    all    ${cidr}    ${DB_AUTH_METHOD}"
    if ! grep -Fq "${entry}" "${PG_HBA_CONF}"; then
        printf '\n%s\n' "${entry}" >> "${PG_HBA_CONF}"
    fi
}

ensure_pg_hba_entry "127.0.0.1/32"
ensure_pg_hba_entry "172.16.0.0/12"
ensure_pg_hba_entry "192.168.0.0/16"
ensure_pg_hba_entry "10.0.0.0/8"

systemctl restart postgresql
systemctl --no-pager --full status postgresql | sed -n '1,20p'
ss -ltnp | grep ':5432' || true

echo
echo "PostgreSQL is configured for Docker bridge access."
echo "listen_addresses=${POSTGRES_LISTEN_ADDRESS}"
echo "pg_hba rules added for 127.0.0.1/32, 172.16.0.0/12, 192.168.0.0/16, 10.0.0.0/8"