"""P2 settings from environment variables. The data platform's settings (DATA_DIR etc.) are shared."""
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORM = os.path.abspath(os.path.join(ROOT, "..", "vastra-data-platform"))

# The data platform reads these; point them at the platform folder unless already set.
os.environ.setdefault("DATA_DIR", os.path.join(PLATFORM, "data"))
os.environ.setdefault("CONTRACTS_DIR", os.path.join(PLATFORM, "contracts"))
os.environ.setdefault("REFERENCE_DIR", os.path.join(PLATFORM, "reference"))
os.environ.setdefault("REPORTS_DIR", os.path.join(PLATFORM, "reports"))


@dataclass
class Settings:
    out_dir: str = field(default_factory=lambda: os.getenv("P2_OUT_DIR", os.path.join(ROOT, "out")))
    top_k: int = field(default_factory=lambda: int(os.getenv("P2_TOP_K", "40")))   # rows planners review a day
    test_weeks: int = field(default_factory=lambda: int(os.getenv("P2_TEST_WEEKS", "8")))
    # Kill switch: "rule" returns the planner to the rule list at once. The business never depends on the model.
    mode: str = field(default_factory=lambda: os.getenv("P2_MODE", "model"))
    gate_recall_points: float = field(default_factory=lambda: float(os.getenv("P2_GATE_POINTS", "10")))
    actor: str = field(default_factory=lambda: os.getenv("VASTRA_DATA_USER", "p2-job"))

    @property
    def model_dir(self):
        return os.path.join(self.out_dir, "model")

    @property
    def lists_dir(self):
        return os.path.join(self.out_dir, "risk_lists")


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
