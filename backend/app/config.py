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
    initial_fetch: int = 3000  # newest mails pulled on first sync
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


settings = Settings()
