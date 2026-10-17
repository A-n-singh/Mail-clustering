# Mail-clustering
This depository is built to ease out the biggest problem of of every corporate individual that is male. There are too many mails unwanted mails. Sometimes people think that they should be clustered. So this repository is the exact problem.

## Phase 1 backend (`backend/`)
FastAPI + SQLAlchemy. Google sign-in (owner-only), Gmail sync, sender grouping, rule-based scoring,
actions with Undo. Each cluster keeps at most `CLUSTER_CAP` (360) live mails; trashing refills it with older mail from the same sender. Run: `cd backend && pip install -r requirements.txt && cp .env.example .env && uvicorn app.main:app --reload`.
Tests: `pytest`.

## Run it
```bash
# backend
cd backend && pip install -r requirements.txt && cp .env.example .env   # fill in Google + keys
uvicorn app.main:app --reload
# frontend
cd frontend && npm install && npm run dev        # http://localhost:5173
# try it with fake mail and no Google credentials
cd backend && python demo.py                     # then open the frontend and click Sign in
# Postgres instead of SQLite
docker compose up -d db   # DATABASE_URL=postgresql+psycopg://inbox:inbox@localhost:5432/inbox
```
Tests: `cd backend && pytest` · `cd frontend && npm test`.

### Permanent delete (optional)
Off by default. Set `ALLOW_PERMANENT_DELETE=true` and sign in again: Google then asks for full-mailbox access
(`https://mail.google.com/`), the only scope that allows it. The **Trash** tab can then delete mail that is already in
Trash, after you type `DELETE FOREVER`. Mail restored since the last sync is skipped. Without it, Gmail empties Trash after 30 days.

## Phase 2: topic clusters and the map
Mail is also grouped **by topic across senders**. Each mail is turned into a vector by a small sentence model that runs
**on this machine** (`fastembed`, ~130 MB downloaded on first use; nothing is sent anywhere, no LLM). Vectors are reduced with
UMAP, clustered with HDBSCAN, and a mail that sits far from its own cluster goes to the **review bucket**. Topic names are
the most distinctive words in the cluster (no LLM). 2D coordinates are stored, so the **Topic map** tab only reads them.
New mail is placed into the nearest topic after every sync; topics are rebuilt weekly (`RECLUSTER_DAYS`) or with **Rebuild topics**.
Topic IDs stay stable across rebuilds. Lasso-select dots to trash/archive/star in bulk (same confirm + Undo + protection rules).
