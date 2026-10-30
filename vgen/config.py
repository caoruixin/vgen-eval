"""Runtime settings.

TOKENBAY_* values come only from the workspace .env file (the file is the single
source of truth for the gateway credentials). App-only VGEN_* options may also be
set in the process environment, which wins over the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent

# Endpoint key -> (display name, base-url variable, api-key variable, rpm variable)
ENDPOINT_SPECS: dict[str, tuple[str, str, str, str]] = {
    "com": ("tokenbay.com", "TOKENBAY_BASE_URL", "TOKENBAY_API_KEY", "TOKENBAY_REQUESTS_PER_MINUTE"),
    "me": ("tokenbay.me", "TOKENBAY_ME_BASE_URL", "TOKENBAY_ME_API_KEY", "VGEN_RPM_ME"),
}

MOCK_BASE_URL = "http://mock.tokenbay.local"


@dataclass(frozen=True)
class Endpoint:
    key: str
    name: str
    base_url: str
    api_key: str = field(repr=False)
    rpm: int = 15
    trust_env: bool = True


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    endpoints: dict[str, Endpoint]
    mock: bool
    host: str
    port: int
    budget_usd: float
    hard_cap_usd: float | None
    confirm_above_usd: float
    usd_cny: float
    judge_model: str
    judge_endpoint: str
    max_inflight: int
    poll_min_s: float
    first_poll_cap_s: float
    gen_timeout_s: float
    edit_timeout_s: float
    submit_read_timeout_s: float

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def videos_dir(self) -> Path:
        return self.data_dir / "videos"

    @property
    def frames_dir(self) -> Path:
        return self.data_dir / "frames"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def load_settings(env_file: Path | None = None) -> Settings:
    env_path = env_file or Path(os.environ.get("VGEN_ENV_FILE", ROOT / ".env"))
    file_values = {k: (v or "") for k, v in dotenv_values(env_path).items()} if env_path.exists() else {}

    def vgen(name: str, default: str | None = None) -> str | None:
        if name in os.environ:
            return os.environ[name]
        return file_values.get(name, default)

    mock = _truthy(vgen("VGEN_MOCK"))
    enabled = [k.strip() for k in (vgen("VGEN_ENDPOINTS", "com") or "com").split(",") if k.strip()]
    trust_env = _truthy(file_values.get("TOKENBAY_HTTP_TRUST_ENV", "true"))

    endpoints: dict[str, Endpoint] = {}
    for key in enabled:
        if key not in ENDPOINT_SPECS:
            raise ValueError(f"Unknown endpoint '{key}' in VGEN_ENDPOINTS (known: {', '.join(ENDPOINT_SPECS)})")
        name, url_var, key_var, rpm_var = ENDPOINT_SPECS[key]
        rpm_raw = vgen(f"VGEN_RPM_{key.upper()}") or file_values.get(rpm_var) or "15"
        rpm = max(1, int(_float(rpm_raw, 15)))
        if mock:
            endpoints[key] = Endpoint(key, name, MOCK_BASE_URL, "sk-mock", rpm=max(rpm, 120), trust_env=False)
            continue
        base_url = (file_values.get(url_var) or "").strip().rstrip("/")
        api_key = (file_values.get(key_var) or "").strip()
        if not base_url or not api_key:
            raise ValueError(f"{url_var} and {key_var} must be set in {env_path}")
        endpoints[key] = Endpoint(key, name, base_url, api_key, rpm=rpm, trust_env=trust_env)

    # mock runs never share a database (ledger, leaderboard) with real, billed runs
    default_data = ROOT / ("data-mock" if mock else "data")
    data_dir = Path(vgen("VGEN_DATA_DIR", str(default_data)) or default_data)
    hard_cap = vgen("VGEN_HARD_CAP_USD")
    return Settings(
        root=ROOT,
        data_dir=data_dir,
        endpoints=endpoints,
        mock=mock,
        host=vgen("VGEN_HOST", "127.0.0.1") or "127.0.0.1",
        port=int(_float(vgen("VGEN_PORT"), 8765)),
        budget_usd=_float(vgen("VGEN_BUDGET_USD"), 40.0),
        hard_cap_usd=_float(hard_cap, 0.0) if hard_cap not in (None, "") else None,
        confirm_above_usd=_float(vgen("VGEN_CONFIRM_ABOVE_USD"), 3.0),
        usd_cny=_float(vgen("VGEN_USD_CNY"), 7.2),
        judge_model=vgen("VGEN_JUDGE_MODEL", "gemini-3.1-pro-preview") or "gemini-3.1-pro-preview",
        judge_endpoint=vgen("VGEN_JUDGE_ENDPOINT", "com") or "com",
        max_inflight=int(_float(vgen("VGEN_MAX_INFLIGHT"), 6)),
        poll_min_s=1.0 if mock else _float(vgen("VGEN_POLL_MIN_S"), 15.0),
        first_poll_cap_s=1.0 if mock else _float(vgen("VGEN_FIRST_POLL_CAP_S"), 60.0),
        gen_timeout_s=_float(vgen("VGEN_GEN_TIMEOUT_S"), 1200.0),   # Wan 2.7 t2v took 11–14 min (3/3)
        edit_timeout_s=_float(vgen("VGEN_EDIT_TIMEOUT_S"), 900.0),
        submit_read_timeout_s=_float(vgen("VGEN_SUBMIT_READ_TIMEOUT_S"), 150.0),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
