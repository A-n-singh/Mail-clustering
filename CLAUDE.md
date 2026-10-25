# CLAUDE.md: read this first (about 3 minutes). Facts are verified against the code; update this file when you change architecture.

## What this is
**Smart Inbox**: a web app that reads each signed-in user's Gmail mailbox, groups the mail (by sender and by topic), scores it, and lets the user clean
up in bulk with Undo. Built as a small pilot where **anyone who signs in with Google gets their own mailbox in their own database file** (`OPEN_SIGNUP`, capped at `MAX_USERS`=10; invited-only mode still exists); the owner (`OWNER_EMAIL`) is the first user and keeps working exactly as before. Everything runs on the owner's server; no mail text goes to any third party and no cloud LLM is used.
Backend FastAPI + SQLAlchemy (SQLite default, Postgres optional). Frontend React 18 + Vite + d3 + three.js (dark "HUD" theme). Deploy: Docker Compose + Caddy.
Owner talks Hinglish and prefers short plain answers; they decide scope, so propose before building anything not asked for.

## Run and test
```
cd backend && pip install -r requirements.txt && cp .env.example .env && uvicorn app.main:app --reload     # API on :8000
cd frontend && npm install && npm run dev                                                                   # UI on :5173, proxies /api -> :8000
cd backend && python demo.py        # same app with a fake in-memory Gmail + 250 made-up mails, no Google needed (then npm run dev, click Sign in)
cd backend && pytest -q             # 121 tests, ~35 s, all offline (FakeGmail + FakeEmbedder in tests/conftest.py and tests/test_topics.py)
cd frontend && npm test             # vitest, 2 tests;  npx vite build  to check the bundle
python -m app.doctor [--model] [--live]     # plain-language setup check (run inside backend/)
scripts/e2e_*.py                    # Playwright browser checks against demo.py + npm run dev (need `pip install playwright` and /opt/pw-browsers chromium)
```
One uvicorn worker only (job flags in `control.py` are in-process, kept per user). No DB migrations exist: `create_all` only (a NEW table such as `users` is fine), so **never add/rename a
column on an existing table** without writing a migration (it would break every existing database).

## Backend map (`backend/app/`)
| File | Job |
|---|---|
| `main.py` | App, lifespan (create tables, close orphaned runs, scheduler every `SYNC_INTERVAL_MINUTES`), every HTTP route; `_sync_job(key)` (sync -> refill -> `after_sync`), `_sync_all` (scheduler: every user in turn, one at a time), `_on_batch` (topic progress per batch), `_refill_job` (background refill after a trash action) |
| `auth.py`, `security.py` | Google OAuth + PKCE; signed session cookie `{email, uid, dk}`; `current_user` -> `Principal(email, uid, data_key, is_owner)`, `is_allowed`: with `OPEN_SIGNUP=true` any Google account, otherwise only `OWNER_EMAIL` + `ALLOWED_EMAILS` (checked on EVERY request in invited mode); the callback also rejects an unverified Google e-mail (async `current_user` on purpose: it also selects the user's job flags); Fernet-encrypted refresh token |
| `users.py`, `db.py` | **Users**: central `users` table (`User` in `models.py`: id, email, `data_key`, role, created_at, last_login_at) in the main database. **One database per user**: `data_key` is `main` (the owner: the main database, so an existing install moves nothing) or `u<id>` (own SQLite file in `USER_DATA_DIR`, default `<folder of mail.db>/users/`). `get_db` = the signed-in user's own database, `open_user_session(key)` for background jobs, `get_central_db` for the user list, `drop_user_data`. `MAX_USERS` limits sign-ups (owner exempt). Keys are validated (`main` or `u<digits>`). SQLite only for now |
| `config.py` | `Settings` from `.env` (see list below) |
| `gmail.py` | Gmail client behind an interface; paced calls (`GMAIL_RATE_PER_SEC`), retries on rate limits |
| `sync.py`, `sync_store.py`, `control.py` | History-ID incremental sync; resumable first sync; Stop keeps progress; in-memory running/cancel flags **per user** (`control.flags(key)`; `control.cancel_sync` etc. are proxies to the CURRENT user's flags, selected by `control.use`/`scope`; jobs wrap themselves in `control.scope(key)`) |
| `cleaning.py`, `scoring.py` | Strip HTML/quotes/signatures; rule-based class `urgent|needs_reply|fyi|junk` + 0-100 score + reason (`rules-v2`) |
| `grouping.py`, `capacity.py` | Sender groups (domain, or address for free-mail); `subject_key` (digit-words -> `#`); cap 360 live mails per group (`enforce_cap`, `refill`) AND `USER_CAP`=1000 for the whole mailbox (`enforce_user_cap`, `refill_user`, `refill_user_in_batches`: pulls mail OLDER than the oldest kept, 25 at a time) |
| `actions.py`, `purge.py`, `cleanup.py` | trash/archive/star/label/... with batch Undo + action log; bulk needs confirm and is BLOCKED on urgent/needs_reply (`PROTECTED`); guarded permanent delete |
| `embeddings.py` | Local fastembed model (`EMBED_MODEL`, default `BAAI/bge-small-en-v1.5`, 384-d); text = `sender. subject. snippet[:200]`; stores `Embedding.model` per vector |
| `topics.py` | embeddings -> UMAP (10-d to cluster, 3-d to draw) -> HDBSCAN -> outlier demotion -> Review bucket; stable topic ids (>=50% overlap); rebuild at load milestones (30/200/1000 live mails, `progress`), when the mailbox grew 1.5x (`grown_since_build`), weekly, or `/topics/recluster`; `assign_new` for new mail |
| `hier.py` | "Hologram inside hologram": path `t<id>` or `k` (Kinds = all mail by SUBJECT only), `.n` per level; max depth 4; <16 mails = leaf list; in-memory cache, namespaced per user database (mail ids of two users may be equal) |
| `naming.py` | **Topic headings pipeline** (below). Used by `topics._store` and `hier._build` |
| `holo_layout.py` | Deterministic 3D layout for the Groups hologram (groups on a sphere, mails in a ball); no clustering |
| `doctor.py` | Setup check incl. heading model and "Stored vectors" (model-change) rows |
| `models.py` | Per-user database, 12 tables: owner (that user's Google account + encrypted token, one row), messages, scores, clusters(+message_cluster), actions, sync_state, sync_runs, embeddings, topics(+message_topic), topic_runs. Plus the central `users` table in the main database |

Routes (all but `/health` and `/auth/*` need the signed-in cookie and act only on the caller's own mailbox): `/auth/login|callback|logout`, `/me`, `DELETE /account`; `/sync`, `/sync/stop`, `/sync/status` (also reports `refilling`);
`/summary`, `/needs-action`, `/digest`, `/storage`, `/messages/{id}`; `/groups` (+filters), `/groups/hologram`, `/groups/{id}/hologram|subjects|messages`;
`/actions`, `/actions/preview`, `/actions/{batch}/undo`, `/actions/log`; `/topics`, `/map`, `/topics/status`, `POST /topics/recluster`, `/topics/{id}/messages`;
`/hier/{path}`, `/hier/{path}/messages`; `/trash`, `POST /trash/purge`, `/health`.

Settings (`.env`): DATABASE_URL, TOKEN_KEY, SESSION_SECRET, GOOGLE_CLIENT_ID/SECRET, OAUTH_REDIRECT_URI, FRONTEND_URL, OWNER_EMAIL, OPEN_SIGNUP (default false in code; the `.env.example` files set true), ALLOWED_EMAILS (comma list, invited mode only), MAX_USERS=10, USER_DATA_DIR, INITIAL_FETCH=1000,
SYNC_INTERVAL_MINUTES=20, ALLOW_PERMANENT_DELETE=false, TOPICS_ENABLED, EMBED_MODEL, TOPIC_MIN_CLUSTER_SIZE=8, TOPIC_MIN_MESSAGES=30, RECLUSTER_DAYS=7,
GMAIL_RATE_PER_SEC=8, CLUSTER_CAP=360, USER_CAP=1000, BATCH_SIZE=25, TOPIC_MILESTONES="30,200,1000", TITLE_MODEL_PATH (optional .gguf), TITLE_MIN_SIMILARITY=0.75, TITLE_MERGE_SIMILARITY=0.93.

## Frontend map (`frontend/src/`)
`main.jsx` -> `App.jsx` (Login, Dashboard with header + 8 tabs: Needs action, Groups, Topic map, Daily digest, Storage, Trash, Action log, Account; also `Groups`, `Drill`,
`TopicMap`, `SubjectRow`, `Digest`, ...). `Hologram.jsx` = the three.js 3D view used by BOTH Groups and Topic map (props: points, topics, focus, selected, onSelect,
onOpen, onDrill, far). Interaction: drag orbit, pinch/wheel zoom, tap a ball = camera flies to it, tap it again = go inside, tap empty space = fly back, tap a dot =
open that mail, Lasso = polygon select for bulk actions. `MailList.jsx` (MailRow, Reader = cleaned text, `useMailBody`), `Filters.jsx`, `Explain.jsx` ("Read how this
works" text per tab; update it when behaviour changes), `ClusterMap.jsx` (only its `GREY`/`topicColor` exports are used now), `api.js` (fetch wrapper, base `/api`).
`Bubbles.jsx` is **dead code** (the flat bubble view was replaced by the Groups hologram). The flat 2D map view was removed on purpose; Topic map = Hologram 3D + List.

## Key pipelines
- **Sync**: Gmail history IDs; first run fetches newest `INITIAL_FETCH`; regroup while syncing; every `BATCH_SIZE` (25) mails `topics.progress` embeds them and places them in the nearest topic, and topics are rebuilt from scratch only when the mailbox first reaches a milestone (30/200/1000 live mails); `enforce_cap` then `enforce_user_cap` (whole mailbox keeps at most `USER_CAP`=1000 live mails, protected mail never evicted); after a sync or a trash action `refill_user_in_batches` pulls mail OLDER than the oldest kept (25 at a time) back up to the cap; Stop/failed keeps what was read and resumes. After a sync, `topics.after_sync`.
- **Topics**: while mail loads, `progress` (called every `BATCH_SIZE` mails and after each refill batch) embeds the new mail and calls `assign_new`, or a full `recluster` the first time the live count reaches a milestone in `TOPIC_MILESTONES`. After a sync, `after_sync` -> if any live mail has a vector from another model (`stale_embeddings`) OR a rebuild is due (>7 days / never) OR the mailbox grew >=1.5x since the last build -> full `recluster`; else `assign_new`. A failing `progress` never stops mail loading (caught in `sync.run_sync` and `main._on_batch`).
  **Changing `EMBED_MODEL` recomputes every vector and rebuilds topics at the next sync** (idempotent, resumable, trashed mail not counted). `load_matrix(..., model=)` never mixes models.
- **Headings (`naming.py`)**: facts (subject types, order numbers collapsed) -> merge near-copies by embedding (cos >= 0.93) -> router (one type >= 70%: that subject is the heading, no model)
  -> else pick <= 5 subjects by meaning with counts -> optional local Qwen2.5-1.5B-Instruct (llama-cpp-python) writes <= 6 words -> rule-based checker (empty/long/digits/sender-only/alarm words without
  urgent mail/copies one subject/no shared word AND not close in meaning (cos >= 0.75)) -> one retry with a stricter prompt -> most typical real subject -> old keyword label.
  The model is OPTIONAL (`TITLE_MODEL_PATH`, `pip install -r requirements-naming.txt`, `python fetch_title_model.py`), loaded only during a naming pass. It never trains. Summaries are NOT model-written.
- **Groups hologram**: `/groups/hologram` = one ball per sender, `/groups/{id}/hologram` = one ball per subject kind (top 12 `subject_key`s); layout from `holo_layout.py`; opening a group also shows the mail list.
- **Protection rule**: urgent and needs_reply mail can never be bulk-trashed/archived or evicted by the cap. Enforced in code (`actions.py`, `capacity.py`), not in the UI or a model.

## Decisions already made (do not re-litigate without the owner)
- Embedding model stays `BAAI/bge-small-en-v1.5`. Benchmarked 9 models on 2,040 labelled emails (table in `README.md`): others were only ~1-4 points better at 3-9x the size/time. The owner has NOT decided to switch to any other model and no switch is planned; do not propose one unless asked. Changing `EMBED_MODEL` is handled by the guard above.
- Heading model is Qwen2.5-**1.5B** (0.5B was too weak). NO larger fallback model (4B removed: server capacity). No cloud LLM. Owner chose NOT to build: model-written right-panel summaries, rename-by-hand,
  or learning from corrections (only discussed; a simple version would be stored overrides + few-shot examples).
- Colour rule in the agreed redesign: state hues (urgent red, needs-reply amber, FYI grey, junk violet) appear ONLY on tags/score rings/pips; cluster colours are cool blues/violets/greens (identity only).

## Pilot direction (discussed with the owner)
Goal: a small pilot, at most **10 users at a time**, not a public launch. Built and tested (with fake Gmail only):
- Mailbox rules: 1,000 live mails per mailbox (`USER_CAP`), loaded and embedded in batches of 25 (`BATCH_SIZE`), topics grown batch by batch and rebuilt only at milestones, refill with older mail as mail leaves. Reasoning: a person cannot view 1,000 mails, he views clusters.
- **Users**: `users` table + **one database file per user** (see `users.py`, `db.py`, `auth.py` above). **The owner decided sign-up is open: anyone who logs in with Google gets access to their own mailbox, no address list** (`OPEN_SIGNUP=true`; do NOT turn this back into an allow-list design). `MAX_USERS` still caps accounts (`PilotFull` -> 403 "The pilot is full"). `OPEN_SIGNUP=false` keeps the older invited mode (`ALLOWED_EMAILS`). The code default is false so an existing install does not become open by accident; the deploy `.env` must say `OPEN_SIGNUP=true`. Account deletion revokes the token, erases the data, deletes the user's file, takes them off the list and clears the cookie.
- **Decision (chosen over `user_id` columns on every table):** a separate database per user. Reason: isolation is by construction (no forgotten `WHERE user_id` can leak mail), no existing table changes (no migration needed), "delete my data" = delete a file, SQLite writers never contend across users. Trade-offs: SQLite only (`_engine_for` raises for a non-SQLite `DATABASE_URL`), many small files do not scale to thousands of users, no cross-user queries. If the app ever outgrows this, move to Postgres with a `user_id` column + migrations then.
Hosting: the app stays on the owner's existing Google Cloud VM (Docker Compose + Caddy, see `deploy/README.md`); scale it up by resizing the VM. `deploy/backup.sh` now backs up every user database.
NOT built yet (propose, do not start): a real job queue + separate worker with fair round-robin turns (today: the scheduler syncs users one after another, but "Sync now" and refills from several users can run at the same time in this one process), per-user Gmail pacing (the pacer in `gmail.py` is shared by the whole process, which is safe for Google's quota), an admin screen / disabling a user without editing `.env`, Postgres for several users, Alembic migrations, (open sign-up is built now).
**Google decides who can reach the sign-in page, whatever this app allows** (verify all of this in Google's current docs): in *Testing* status only listed Test users (max 100) can sign in and their sign-in expires after about 7 days; for ANYONE to sign in the OAuth app must be set to **In production** (Google Auth platform > Audience > Publish app), with a privacy policy and terms page and an authorised domain on the consent screen. While the app is unverified, users see an "unverified app" warning and Google caps the app at about 100 users in total; beyond that Google must verify the restricted `gmail.modify` scope, which needs a paid third-party security assessment (CASA). Scopes stay `openid`, `email`, `gmail.modify`; redirect URI and Gmail API unchanged. Then set `OPEN_SIGNUP=true` (and `MAX_USERS`) in `deploy/.env` and run `docker compose up -d`.
Open questions the owner has not answered: whether to go through Google verification (needed beyond about 100 users); free or paid; heading model in the pilot or real subjects only; Hindi/Hinglish mail expected or not; domain and (for later) a separate worker VM.
Gmail limits (from memory, verify in Google's docs): about 250 quota units/second per user; reading or listing mail costs about 5 units; the app paces 8 calls/s (about 40 units/s) and retries on rate-limit errors.

## Not built / known gaps
- The redesigned UI (night-sky look, right-hand inspector panel, Overview) exists only as a mock-up artifact (https://claude.ai/artifact/PrxV6k9etpHcqQuMPVsnjd, may be stale); the real app still has the older HUD layout. Apply it as a restyle, keeping every feature.
- Tap-to-fly and the holograms were tested with a mouse in Chromium, not on a real tablet. The Docker image does not include llama-cpp or a title model (headings fall back to real subjects).
- Several-user behaviour (sign-in callback, isolation, per-user flags, deletion) is covered by unit/API tests with a fake Gmail and a faked Google flow; **sign-in with two real Google accounts has not been tried**. A deleted user can sign in again and gets a fresh empty mailbox.
- The refill uses Gmail's `before:<epoch seconds>` search operator; tested only against `FakeGmail` (which parses it), not against real Gmail yet. Batch loading, caps and milestones were verified by unit tests only, not in a browser or with a real mailbox.
- Hindi/Hinglish mail is untested (embedding model is English). Thresholds 0.93 / 0.75 were tuned on small samples. `Bubbles.jsx` unused.

## Working agreements
Default branch `main`; the owner merges PRs on GitHub and branches are deleted after merge. Work on a new `claude/...` branch, commit with a clear message, push, and ask before opening a PR. Never push to `main`.
Only commit what was asked. Report test results honestly (what ran, what did not). Keep docs current: this file, `README.md`, `deploy/README.md`, `Explain.jsx`.
