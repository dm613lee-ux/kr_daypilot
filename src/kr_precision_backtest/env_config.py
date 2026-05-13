from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KisCredentials:
    provider: str
    app_key: str
    app_secret: str
    env_dv: str


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_quotes(value.strip())
    return values


def load_kis_credentials(path: Path) -> KisCredentials:
    env = load_env_file(path)
    provider = env.get("BROKER_API_PROVIDER", "").strip().lower()
    app_key = env.get("BROKER_API_KEY", "").strip()
    app_secret = env.get("BROKER_API_SECRET", "").strip()
    env_dv = env.get("BROKER_API_ENV") or env.get("KIS_ENV") or "real"
    env_dv = env_dv.strip().lower()

    if provider and provider != "kis":
        raise ValueError("BROKER_API_PROVIDER is not kis.")
    if not app_key or not app_secret:
        raise ValueError("BROKER_API_KEY and BROKER_API_SECRET are required in KR DayPilot .env.")
    if env_dv not in {"real", "demo"}:
        raise ValueError("KIS environment must be real or demo.")
    return KisCredentials(provider=provider or "kis", app_key=app_key, app_secret=app_secret, env_dv=env_dv)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value

