from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def app_dir() -> Path:
    """Where settings, tiers.json, releases.csv and the database live.

    Next to the .exe when frozen, so everything stays in one folder the user
    can find; RESELL_RADAR_HOME overrides it (tests, custom setups).
    """
    if os.environ.get("RESELL_RADAR_HOME"):
        path = Path(os.environ["RESELL_RADAR_HOME"])
    elif getattr(sys, "frozen", False):
        path = Path(sys.executable).parent
    else:
        path = Path.cwd() / "resell_radar_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    telegram_token: str = ""
    telegram_chat_id: str = ""
    min_profit: int = 30_000
    min_profit_ratio: float = 0.20
    sell_fee_rate: float = 0.06
    shipping_cost: int = 4_000
    notify_maybe: bool = False  # also alert on 애매 items, not just 추천
    remind_hours_before_end: int = 3
    sources: dict = field(default_factory=lambda: {"nike": True, "us_calendar": True, "musinsa": True, "manual": True})

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


def load_settings(path: Path) -> Settings | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
    return Settings(**known)


def save_settings(path: Path, settings: Settings) -> None:
    path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
