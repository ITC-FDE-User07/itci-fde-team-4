"""Settings come from environment variables (same rule as the Week 1 API: nothing secret in a committed file).

Copy .env.example to .env for local work.
"""
import getpass
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(name, default):
    return os.getenv(name, default)


@dataclass
class Settings:
    # Folder holding raw.db, clean.db, ready.db, ops.db, gov.db and restricted/person_tokens.db
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", os.path.join(ROOT, "data")))
    # File drops from POS / ERP / WMS / legacy land here: inbox/<source>/*.jsonl
    inbox_dir: str = field(default_factory=lambda: _env("INBOX_DIR", os.path.join(ROOT, "inbox")))
    reports_dir: str = field(default_factory=lambda: _env("REPORTS_DIR", os.path.join(ROOT, "reports")))
    contracts_dir: str = field(default_factory=lambda: _env("CONTRACTS_DIR", os.path.join(ROOT, "contracts")))
    reference_dir: str = field(default_factory=lambda: _env("REFERENCE_DIR", os.path.join(ROOT, "reference")))
    # Week 1 API integration feed (the outbox). The data platform is just another consumer of /v1/integration/events.
    api_url: str = field(default_factory=lambda: _env("VASTRA_API_URL", "http://127.0.0.1:8200"))
    api_key: str = field(default_factory=lambda: _env("VASTRA_API_KEY", "dev-data-key"))
    # Read-only legacy database (named read-only access: never the shared vastra_admin login).
    legacy_db_path: str = field(default_factory=lambda: _env(
        "LEGACY_DB_PATH", os.path.join(ROOT, "..", "vastra-legacy-lab", "data", "vastra_legacy.db")))
    # Who is running the job: written to access_log and failed_records.
    actor: str = field(default_factory=lambda: _env("VASTRA_DATA_USER", getpass.getuser() or "system"))
    # Retry policy for temporary problems (database busy): 3 tries, waiting 2, 4, 8 seconds.
    retry_attempts: int = field(default_factory=lambda: int(_env("LOAD_RETRY_ATTEMPTS", "3")))
    retry_base_s: float = field(default_factory=lambda: float(_env("LOAD_RETRY_BASE_S", "2")))
    batch_size: int = field(default_factory=lambda: int(_env("LOAD_BATCH_SIZE", "2000")))
    # Optional: post every alert to a chat/email webhook as well as the log.
    alert_webhook: str = field(default_factory=lambda: _env("ALERT_WEBHOOK_URL", ""))
    # Week 1 cut-over: prices before this date are low-trust (E21); the live ledger starts here.
    week1_cutover: str = field(default_factory=lambda: _env("WEEK1_CUTOVER", "2026-11-10"))

    @property
    def alert_log(self):
        return os.path.join(self.data_dir, "alerts.log")


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
