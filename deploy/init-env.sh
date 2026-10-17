#!/usr/bin/env bash
# Creates deploy/.env if missing and fills TOKEN_KEY and SESSION_SECRET if they are empty. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
fill() { # fill KEY VALUE: only if the key is currently empty
  if grep -qE "^$1=$" .env; then sed -i "s|^$1=$|$1=$2|" .env; echo "generated $1"; else echo "$1 already set, left alone"; fi
}
# a Fernet key is 32 random bytes in url-safe base64
fill TOKEN_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"
fill SESSION_SECRET "$(openssl rand -hex 32)"
chmod 600 .env
echo "Now edit deploy/.env and fill DOMAIN, the two redirect/frontend URLs, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and OWNER_EMAIL."
