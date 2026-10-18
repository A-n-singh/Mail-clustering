# Mail-clustering
This repository is built to ease out the biggest problem of of every corporate individual that is male. There are too many mails unwanted mails. Sometimes people think that they should be clustered. So this repository is the exact problem.

**Working on this repo (people or AI assistants):** read [`CLAUDE.md`](CLAUDE.md) first. It is the verified map of the code, the decisions already made
and what is not built yet. Deploying on a server: [`deploy/README.md`](deploy/README.md).

## What you get
Eight tabs: **Needs action** (urgent / needs-reply mail first), **Groups** (3D hologram, one ball per sender; going inside shows one ball per kind of subject, with
the mail list and bulk actions), **Topic map** (mail grouped by meaning across senders: Hologram 3D or List, Kinds mode, Rebuild topics, go inside a topic to split it
again), **Daily digest**, **Storage**, **Trash** (optional permanent delete), **Action log** (Undo per batch), **Account** (erase my data). Click a subject anywhere to read
the cleaned text; Sync now / Stop in the header. Urgent and needs-reply mail is protected from bulk actions.

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
**on this machine** (`fastembed`, about 0.1 GB downloaded on first use; nothing is sent anywhere). Vectors are reduced with
UMAP, clustered with HDBSCAN, and a mail that sits far from its own cluster goes to the **review bucket**. Topics are named by the pipeline in *Topic headings* below.
3D coordinates are stored, so the map tabs only read them.
New mail is placed into the nearest topic after every sync; topics are rebuilt weekly (`RECLUSTER_DAYS`) or with **Rebuild topics**.
Topic IDs stay stable across rebuilds. Lasso-select dots to trash/archive/star in bulk (same confirm + Undo + protection rules).

### Changing the embedding model
`EMBED_MODEL` can be changed at any time (the default `BAAI/bge-small-en-v1.5` was kept after a benchmark of nine models). Each stored vector remembers
which model made it. At the next sync, if any mail carries a vector from a different model, **every vector is recomputed and the topics are rebuilt
right away** (not at the weekly rebuild), so vectors of two models are never mixed. It takes a few minutes for a few thousand mails, can be stopped and
resumed (only what is still stale is redone), and settles after one rebuild. `python -m app.doctor` shows whether your stored vectors match.

### Look and feel
The dashboard uses a dark "HUD" theme. **Topic map → Hologram 3D** shows every mail as a glowing node in 3D, each topic wrapped in a
rotating wireframe shell with a floating label. Drag to orbit, pinch or wheel to zoom, hover to scan a mail, **tap a ball to fly the camera to it, tap it again to go
inside, tap empty space to fly back**, tap a dot to open that mail, and use **Lasso** to select nodes for bulk actions. A plain list is one click away. The **Groups** tab is a hologram too: one ball per sender, and going inside a sender shows one ball per kind of subject.

### Topic headings
A topic is named by a small fixed pipeline, `backend/app/naming.py`:
1. **Facts.** Subjects are counted by type, with order numbers collapsed ("Your order #123 has shipped" and "#456" are one type).
2. **Merge near-copies.** With the embedding model the topics already use, types that mean the same ("Your order has shipped" / "Your order has
   been shipped") are counted as one. Only close copies merge (`TITLE_MERGE_SIMILARITY`, default 0.93), never different themes.
3. **Router.** If one type covers at least 70% of the topic, that subject (numbers dropped) is the heading. No model is used.
4. **Pick by meaning.** Otherwise the model is shown up to 5 subjects, one per theme and biggest first, each with its mail count.
5. **Namer.** A local model (Qwen2.5-1.5B-Instruct) writes at most 6 words.
6. **Checker.** Plain code rejects an empty or long heading, numbers, a bare sender name, alarm words when the topic has no urgent mail, a heading
   that just copies one subject of a mixed topic, or one that neither shares a word with the subjects nor is close to them in meaning
   (`TITLE_MIN_SIMILARITY`, default 0.75).
7. **Retry once, then a real subject.** The model gets one more try with a different prompt; after that the heading is the most typical real
   subject. The old keyword label is the last resort.

The model is optional and never trains or learns. It is loaded only while topics are being named, then freed. Without it, mixed topics are named
by their most typical subject, and steps 1 to 3 still work. To switch it on (inside `backend/`): `pip install -r requirements-naming.txt`,
`python fetch_title_model.py` (about 1.1 GB, one download), then set `TITLE_MODEL_PATH` in `.env` to the path it prints. Everything then runs on
your machine. `python -m app.doctor` shows its status. Any small chat GGUF file works in its place.
