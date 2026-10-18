# Mail-clustering
This repository is built to ease out the biggest problem of of every corporate individual that is male. There are too many mails unwanted mails. Sometimes people think that they should be clustered. So this repository is the exact problem.

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

### Look and feel
The dashboard uses a dark "HUD" theme. **Topic map → Hologram 3D** shows every mail as a glowing node in 3D, each topic wrapped in a
rotating wireframe shell with a floating label. Drag to orbit, wheel to zoom, hover to scan a mail, click to open it, and use
**Lasso** to select nodes for bulk actions. A plain list is one click away. The **Groups** tab is a hologram too: one ball per sender, and going inside a sender shows one ball per kind of subject.

### Topic headings
A topic is named by a small fixed pipeline, `backend/app/naming.py`:
1. **Facts.** Subjects are counted by type, with order numbers collapsed ("Your order #123 has shipped" and "#456" are one type).
2. **Router.** If one subject type covers at least 70% of the topic, that subject (numbers dropped) is the heading. No model is used.
3. **Namer.** Otherwise a local model (Qwen2.5-1.5B-Instruct) reads the 5 most typical subjects and writes at most 6 words.
4. **Checker.** Plain code rejects an empty or long heading, numbers, a bare sender name, alarm words when the topic has no urgent mail, a heading
   that shares no word with the subjects, or one that just copies a single subject of a mixed topic.
5. **Retry once, then a larger model, then a real subject.** The main model gets one more try with a different prompt. If that is rejected too,
   a larger model (Qwen3-4B-Instruct-2507) gets one try. If that is rejected, the heading is the most typical real subject. The old keyword
   label is the last resort.

The models are optional and never train or learn. Only one is in memory at a time, and only while topics are being named. Without them, mixed
topics are named by their most typical subject. To switch them on: `pip install -r requirements-naming.txt`, `python fetch_title_model.py`
(about 1.1 GB), and for the larger one `python fetch_title_model.py --large` (about 2.5 GB, needs roughly 3 GB of free memory while it runs),
then set `TITLE_MODEL_PATH` and `TITLE_MODEL_LARGE_PATH` in `.env` to the paths it prints. Everything then runs on your machine.
`python -m app.doctor` shows their status. Any chat GGUF file works in either place.
