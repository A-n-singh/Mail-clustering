#!/usr/bin/env bash
# Asks 4 questions and writes deploy/.env for you (no manual editing). Safe to run again.
set -euo pipefail
umask 077
cd "$(dirname "$0")"
./init-env.sh >/dev/null

clean() { echo "$1" | tr -d '[:space:]'; }
read -rp "Your domain, e.g. myinbox.duckdns.org: " DOMAIN
DOMAIN=$(clean "$DOMAIN" | tr 'A-Z' 'a-z'); DOMAIN=${DOMAIN#https://}; DOMAIN=${DOMAIN#http://}; DOMAIN=${DOMAIN%%/*}
read -rp "Your Gmail address (the only account allowed in): " OWNER; OWNER=$(clean "$OWNER")
read -rp "Google Client ID: " CID; CID=$(clean "$CID")
read -rsp "Google Client secret (typing is hidden): " SECRET; echo; SECRET=$(clean "$SECRET")

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
