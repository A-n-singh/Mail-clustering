#!/usr/bin/env bash
# Asks 4 questions and writes deploy/.env for you (no manual editing). Safe to run again.
set -euo pipefail
umask 077
cd "$(dirname "$0")"
./init-env.sh >/dev/null

clean() { echo "$1" | tr -d '[:space:]'; }

# Google lets you download the OAuth client as a .json file. Upload it with the SSH window's UPLOAD FILE button
# (it lands in your home folder) and this script reads the Client ID and secret from it, so nothing is typed or pasted.
JSON="${1:-}"
if [ -z "$JSON" ]; then
  JSON=$(ls -t "$HOME"/client_secret*.json 2>/dev/null | head -1 || true)
fi
CID=""; SECRET=""
if [ -n "$JSON" ] && [ -f "$JSON" ]; then
  read -r CID SECRET < <(python3 - "$JSON" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d = d.get("web") or d.get("installed") or d
print(d.get("client_id", ""), d.get("client_secret", ""))
PY
) || true
  if [ -n "$CID" ] && [ -n "$SECRET" ]; then echo "Read Client ID and secret from $(basename "$JSON")"; else CID=""; SECRET=""; echo "Could not read $JSON, will ask instead."; fi
fi
read -rp "Your domain, e.g. myinbox.duckdns.org: " DOMAIN
DOMAIN=$(clean "$DOMAIN" | tr 'A-Z' 'a-z'); DOMAIN=${DOMAIN#https://}; DOMAIN=${DOMAIN#http://}; DOMAIN=${DOMAIN%%/*}
read -rp "Your Gmail address (the only account allowed in): " OWNER; OWNER=$(clean "$OWNER")
if [ -z "$CID" ]; then read -rp "Google Client ID: " CID; fi
if [ -z "$SECRET" ]; then read -rsp "Google Client secret (typing is hidden): " SECRET; echo; fi
CID=$(clean "$CID"); SECRET=$(clean "$SECRET")

[[ "$DOMAIN" == *.* ]] || { echo "That does not look like a domain name."; exit 1; }
[[ "$OWNER" == *@* ]] || { echo "That does not look like an email address."; exit 1; }
[[ -n "$CID" && -n "$SECRET" ]] || { echo "Client ID and secret must not be empty."; exit 1; }

set_kv() { awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' .env > .env.tmp && mv .env.tmp .env; }
set_kv DOMAIN "$DOMAIN"
set_kv OAUTH_REDIRECT_URI "https://$DOMAIN/auth/callback"
set_kv FRONTEND_URL "https://$DOMAIN"
set_kv OWNER_EMAIL "$OWNER"
set_kv GOOGLE_CLIENT_ID "$CID"
set_kv GOOGLE_CLIENT_SECRET "$SECRET"
chmod 600 .env
echo
echo "Saved to deploy/.env (private)."
echo "Google Cloud > Clients > your client > Authorized redirect URIs must contain exactly:"
echo "    https://$DOMAIN/auth/callback"
