from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./mail.db"
    # Fernet key used to encrypt the Google refresh token. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_key: str = ""
    session_secret: str = "change-me"
    google_client_id: str = ""
    google_client_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:8000/auth/callback"
    frontend_url: str = "http://localhost:5173"
    # Only this Google account may sign in (single-owner app).
    owner_email: str = ""
    # Pilot: who may sign in. OWNER_EMAIL always may (and is the admin); ALLOWED_EMAILS = more Google accounts, comma separated.
    # OPEN_SIGNUP=true: ANY Google account may sign in and gets their own mailbox (up to MAX_USERS people). false = only OWNER_EMAIL + ALLOWED_EMAILS.
    open_signup: bool = False
    allowed_emails: str = ""
    max_users: int = 10  # at most this many accounts can ever sign up (the owner is always let in)
    # Each user's mail lives in their own database file, so one user can never see another's data. Empty = a "users" folder
    # next to the main database file (the owner keeps using the main database, so nothing has to be moved).
    user_data_dir: str = ""
    initial_fetch: int = 1000  # newest mails pulled on first sync
    user_cap: int = 1000  # max live mails kept locally in total; older mail is pulled in again as mail leaves
    batch_size: int = 25  # mails read, embedded and placed into topics per batch while loading
    topic_milestones: str = "30,200,1000"  # live-mail counts at which topics are rebuilt from scratch while loading
    sync_interval_minutes: int = 20  # scheduled sync (15-30 min)
    # Permanent delete of mail ALREADY IN TRASH. Off by default: turning it on makes sign-in ask Google for the
    # full-mailbox scope (https://mail.google.com/), which is the only scope that allows permanent deletion.
    allow_permanent_delete: bool = False
    # Phase 2: topic clusters from local embeddings (nothing leaves this machine)
    topics_enabled: bool = True
    embed_model: str = "BAAI/bge-small-en-v1.5"
    topic_min_cluster_size: int = 8
    topic_min_messages: int = 30  # don't cluster tiny mailboxes
    recluster_days: int = 7
    gmail_rate_per_sec: float = 8  # pace Gmail calls; Gmail limits quota units per minute per user
    cluster_cap: int = 360  # max mails kept per cluster
    # Optional local model that writes topic headings for mixed clusters (a .gguf file, e.g. Qwen2.5-1.5B-Instruct Q4_K_M).
    # Empty = no model: mixed clusters are then named by their most typical real subject. See README.
    title_model_path: str = ""
    title_min_similarity: float = 0.75   # a heading with no word in common with the subjects must be at least this close in meaning
    title_merge_similarity: float = 0.93  # two subject types at least this close in meaning are counted as one (near-copies only)


settings = Settings()
