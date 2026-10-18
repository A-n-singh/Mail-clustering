# CLAUDE.md: read this first (about 3 minutes). Facts are verified against the code; update this file when you change architecture.

## What this is
**Smart Inbox**: a single-owner web app that reads ONE Gmail mailbox, groups the mail (by sender and by topic), scores it, and lets the owner clean
up in bulk with Undo. Everything runs on the owner's machine; no mail text goes to any third party and no cloud LLM is used.
Backend FastAPI + SQLAlchemy (SQLite default, Postgres optional). Frontend React 18 + Vite + d3 + three.js (dark "HUD" theme). Deploy: Docker Compose + Caddy.
Owner talks Hinglish and prefers short plain answers; they decide scope, so propose before building anything not asked for.

## Run and test
```
cd backend && pip install -r requirements.txt && cp .env.example .env && uvicorn app.main:app --reload     # API on :8000
cd frontend && npm install && npm run dev                                                                   # UI on :5173, proxies /api -> :8000
cd backend && python demo.py        # same app with a fake in-memory Gmail + 250 made-up mails, no Google needed (then npm run dev, click Sign in)
cd backend && pytest -q             # 98 tests, ~35 s, all offline (FakeGmail + FakeEmbedder in tests/conftest.py and tests/test_topics.py)
cd frontend && npm test             # vitest, 2 tests;  npx vite build  to check the bundle
python -m app.doctor [--model] [--live]     # plain-language setup check (run inside backend/)
scripts/e2e_*.py                    # Playwright browser checks against demo.py + npm run dev (need `pip install playwright` and /opt/pw-browsers chromium)
```
One uvicorn worker only (sync/topic flags in `control.py` are in-process). No DB migrations exist: `Base.metadata.create_all` only, so **never add/rename a
column on an existing table** without writing a migration (it would break every existing database).

## Backend map (`backend/app/`)
| File | Job |
|---|---|
| `main.py` | App, lifespan (create tables, close orphaned runs, scheduler every `SYNC_INTERVAL_MINUTES`), every HTTP route |
| `auth.py`, `security.py` | Google OAuth + PKCE, signed session cookie, owner-only check; Fernet-encrypted refresh token |
| `config.py` | `Settings` from `.env` (see list below) |
| `gmail.py` | Gmail client behind an interface; paced calls (`GMAIL_RATE_PER_SEC`), retries on rate limits |
| `sync.py`, `sync_store.py`, `control.py` | History-ID incremental sync; resumable first sync; Stop keeps progress; in-memory running/cancel flags |
| `cleaning.py`, `scoring.py` | Strip HTML/quotes/signatures; rule-based class `urgent|needs_reply|fyi|junk` + 0-100 score + reason (`rules-v2`) |
| `grouping.py`, `capacity.py` | Sender groups (domain, or address for free-mail); `subject_key` (digit-words -> `#`); cap 360 live mails per group, refill from Gmail |
| `actions.py`, `purge.py`, `cleanup.py` | trash/archive/star/label/... with batch Undo + action log; bulk needs confirm and is BLOCKED on urgent/needs_reply (`PROTECTED`); guarded permanent delete |
| `embeddings.py` | Local fastembed model (`EMBED_MODEL`, default `BAAI/bge-small-en-v1.5`, 384-d); text = `sender. subject. snippet[:200]`; stores `Embedding.model` per vector |
| `topics.py` | embeddings -> UMAP (10-d to cluster, 3-d to draw) -> HDBSCAN -> outlier demotion -> Review bucket; stable topic ids (>=50% overlap); weekly rebuild or `/topics/recluster`; `assign_new` for new mail |
| `hier.py` | "Hologram inside hologram": path `t<id>` or `k` (Kinds = all mail by SUBJECT only), `.n` per level; max depth 4; <16 mails = leaf list; in-memory cache |
| `naming.py` | **Topic headings pipeline** (below). Used by `topics._store` and `hier._build` |
| `holo_layout.py` | Deterministic 3D layout for the Groups hologram (groups on a sphere, mails in a ball); no clustering |
| `doctor.py` | Setup check incl. heading model and "Stored vectors" (model-change) rows |
| `models.py` | 12 tables: owner, messages, scores, clusters(+message_cluster), actions, sync_state, sync_runs, embeddings, topics(+message_topic), topic_runs |

Routes (all but `/health` and `/auth/*` need the owner cookie): `/auth/login|callback|logout`, `/me`, `DELETE /account`; `/sync`, `/sync/stop`, `/sync/status`;
`/summary`, `/needs-action`, `/digest`, `/storage`, `/messages/{id}`; `/groups` (+filters), `/groups/hologram`, `/groups/{id}/hologram|subjects|messages`;
`/actions`, `/actions/preview`, `/actions/{batch}/undo`, `/actions/log`; `/topics`, `/map`, `/topics/status`, `POST /topics/recluster`, `/topics/{id}/messages`;
`/hier/{path}`, `/hier/{path}/messages`; `/trash`, `POST /trash/purge`, `/health`.

Settings (`.env`): DATABASE_URL, TOKEN_KEY, SESSION_SECRET, GOOGLE_CLIENT_ID/SECRET, OAUTH_REDIRECT_URI, FRONTEND_URL, OWNER_EMAIL, INITIAL_FETCH=3000,
SYNC_INTERVAL_MINUTES=20, ALLOW_PERMANENT_DELETE=false, TOPICS_ENABLED, EMBED_MODEL, TOPIC_MIN_CLUSTER_SIZE=8, TOPIC_MIN_MESSAGES=30, RECLUSTER_DAYS=7,
GMAIL_RATE_PER_SEC=8, CLUSTER_CAP=360, TITLE_MODEL_PATH (optional .gguf), TITLE_MIN_SIMILARITY=0.75, TITLE_MERGE_SIMILARITY=0.93.

## Frontend map (`frontend/src/`)
`main.jsx` -> `App.jsx` (Login, Dashboard with header + 8 tabs: Needs action, Groups, Topic map, Daily digest, Storage, Trash, Action log, Account; also `Groups`, `Drill`,
`TopicMap`, `SubjectRow`, `Digest`, ...). `Hologram.jsx` = the three.js 3D view used by BOTH Groups and Topic map (props: points, topics, focus, selected, onSelect,
onOpen, onDrill, far). Interaction: drag orbit, pinch/wheel zoom, tap a ball = camera flies to it, tap it again = go inside, tap empty space = fly back, tap a dot =
open that mail, Lasso = polygon select for bulk actions. `MailList.jsx` (MailRow, Reader = cleaned text, `useMailBody`), `Filters.jsx`, `Explain.jsx` ("Read how this
works" text per tab; update it when behaviour changes), `ClusterMap.jsx` (only its `GREY`/`topicColor` exports are used now), `api.js` (fetch wrapper, base `/api`).
`Bubbles.jsx` is **dead code** (the flat bubble view was replaced by the Groups hologram). The flat 2D map view was removed on purpose; Topic map = Hologram 3D + List.

## Key pipelines
- **Sync**: Gmail history IDs; first run fetches newest `INITIAL_FETCH`; regroup while syncing; `enforce_cap`; Stop/failed keeps what was read and resumes. After a sync, `topics.after_sync`.
- **Topics**: `after_sync` -> if any live mail has a vector from another model (`stale_embeddings`) OR a rebuild is due (>7 days / never) -> full `recluster`; else `assign_new`.
  **Changing `EMBED_MODEL` recomputes every vector and rebuilds topics at the next sync** (idempotent, resumable, trashed mail not counted). `load_matrix(..., model=)` never mixes models.
- **Headings (`naming.py`)**: facts (subject types, order numbers collapsed) -> merge near-copies by embedding (cos >= 0.93) -> router (one type >= 70%: that subject is the heading, no model)
  -> else pick <= 5 subjects by meaning with counts -> optional local Qwen2.5-1.5B-Instruct (llama-cpp-python) writes <= 6 words -> rule-based checker (empty/long/digits/sender-only/alarm words without
  urgent mail/copies one subject/no shared word AND not close in meaning (cos >= 0.75)) -> one retry with a stricter prompt -> most typical real subject -> old keyword label.
  The model is OPTIONAL (`TITLE_MODEL_PATH`, `pip install -r requirements-naming.txt`, `python fetch_title_model.py`), loaded only during a naming pass. It never trains. Summaries are NOT model-written.
- **Groups hologram**: `/groups/hologram` = one ball per sender, `/groups/{id}/hologram` = one ball per subject kind (top 12 `subject_key`s); layout from `holo_layout.py`; opening a group also shows the mail list.
- **Protection rule**: urgent and needs_reply mail can never be bulk-trashed/archived or evicted by the cap. Enforced in code (`actions.py`, `capacity.py`), not in the UI or a model.

## Decisions already made (do not re-litigate without the owner)
- Embedding model stays `BAAI/bge-small-en-v1.5`. Benchmarked 9 models on 2,000 labelled emails: others were only ~1-4 points better at 3-9x the size; gte-base is the first upgrade to try if real clusters look mixed.
- Heading model is Qwen2.5-**1.5B** (0.5B was too weak). NO larger fallback model (4B removed: server capacity). No cloud LLM. Owner chose NOT to build: model-written right-panel summaries, rename-by-hand,
  or learning from corrections (only discussed; a simple version would be stored overrides + few-shot examples).
- Colour rule in the agreed redesign: state hues (urgent red, needs-reply amber, FYI grey, junk violet) appear ONLY on tags/score rings/pips; cluster colours are cool blues/violets/greens (identity only).

## Not built / known gaps
- The redesigned UI (night-sky look, right-hand inspector panel, Overview) exists only as a mock-up artifact (https://claude.ai/artifact/PrxV6k9etpHcqQuMPVsnjd, may be stale); the real app still has the older HUD layout. Apply it as a restyle, keeping every feature.
- Tap-to-fly and the holograms were tested with a mouse in Chromium, not on a real tablet. The Docker image does not include llama-cpp or a title model (headings fall back to real subjects).
- Hindi/Hinglish mail is untested (embedding model is English). Thresholds 0.93 / 0.75 were tuned on small samples. `Bubbles.jsx` unused.

## Working agreements
Default branch `main`; the owner merges PRs on GitHub and branches are deleted after merge. Work on a new `claude/...` branch, commit with a clear message, push, and ask before opening a PR. Never push to `main`.
Only commit what was asked. Report test results honestly (what ran, what did not). Keep docs current: this file, `README.md`, `deploy/README.md`, `Explain.jsx`.
