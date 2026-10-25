#!/usr/bin/env bash
# Consistent copy of ALL databases (the owner's, plus one file per other user) into the current folder: ./smart-inbox-YYYY-MM-DD.tar.gz
set -euo pipefail
cd "$(dirname "$0")"
docker compose exec -T backend python - <<'PY'
import pathlib, sqlite3, tarfile
out = pathlib.Path("/tmp/bk"); out.mkdir(exist_ok=True)
for f in [pathlib.Path("/data/mail.db"), *sorted(pathlib.Path("/data/users").glob("*.db"))]:
    if f.exists():
        s, d = sqlite3.connect(f), sqlite3.connect(out / f.name)
        s.backup(d); d.close(); s.close()
with tarfile.open("/tmp/smart-inbox-backup.tar.gz", "w:gz") as t:
    t.add(out, arcname="databases")
PY
docker compose cp backend:/tmp/smart-inbox-backup.tar.gz "./smart-inbox-$(date +%F).tar.gz"
docker compose exec -T backend sh -c "rm -rf /tmp/bk /tmp/smart-inbox-backup.tar.gz"
echo "saved smart-inbox-$(date +%F).tar.gz  (mail.db = the owner, u<number>.db = the other users)"
