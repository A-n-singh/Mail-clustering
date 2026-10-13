# Mail-clustering
This depository is built to ease out the biggest problem of of every corporate individual that is male. There are too many mails unwanted mails. Sometimes people think that they should be clustered. So this repository is the exact problem.

## Phase 1 backend (`backend/`)
FastAPI + SQLAlchemy. Google sign-in (owner-only), Gmail sync, sender grouping, rule-based scoring,
actions with Undo. Each cluster keeps at most `CLUSTER_CAP` (360) live mails; trashing refills it with older mail from the same sender. Run: `cd backend && pip install -r requirements.txt && cp .env.example .env && uvicorn app.main:app --reload`.
Tests: `pytest`.
