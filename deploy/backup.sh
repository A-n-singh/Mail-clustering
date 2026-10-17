#!/usr/bin/env bash
# Consistent copy of the database into the current folder: ./smart-inbox-YYYY-MM-DD.db
set -euo pipefail
cd "$(dirname "$0")"
docker compose exec -T backend python -c "import sqlite3; s=sqlite3.connect('/data/mail.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close()"
docker compose cp backend:/data/backup.db "./smart-inbox-$(date +%F).db"
docker compose exec -T backend rm -f /data/backup.db
echo "saved smart-inbox-$(date +%F).db"
