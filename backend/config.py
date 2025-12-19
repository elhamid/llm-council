import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0")
    return raw in ("1", "true", "TRUE", "yes", "YES", "on", "ON")


def _parse_csv(s: str) -> List[str]:
    s = (s or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


@dataclass(frozen=True)
class AppConfig:
    """
    Public-beta friendly configuration.

    Safe defaults for a public GitHub repo:
    - auth is OFF unless COUNCIL_API_TOKEN is set
    - CORS is localhost-only unless explicitly opened up
    - rate limiting is ON by default (lightweight in-memory)
    - storage defaults to in-memory (no disk writes unless enabled)
    """

    # Server
    host: str = _env("HOST", "127.0.0.1")
    port: int = _env_int("PORT", 8001)

    # CORS (safe-by-default)
    cors_allow_origins: List[str] = None  # type: ignore[assignment]
    cors_allow_credentials: bool = _env_bool("CORS_ALLOW_CREDENTIALS", True)
    cors_allow_methods: List[str] = None  # type: ignore[assignment]
    cors_allow_headers: List[str] = None  # type: ignore[assignment]

    # Optional API auth for public beta deployments
    # If COUNCIL_API_TOKEN is set, requests must send:
    #   Authorization: Bearer <token>
    # or:
    #   X-API-Key: <token>
    api_token: str = _env("COUNCIL_API_TOKEN", "")

    # Basic abuse resistance
    # - max_request_bytes: reject large JSON bodies (0 disables)
    # - rate_limit_rpm: requests/min per IP (0 disables)
    # - rate_limit_burst: additional burst tokens
    max_request_bytes: int = _env_int("MAX_REQUEST_BYTES", 0)
    rate_limit_rpm: int = _env_int("RATE_LIMIT_RPM", 120)
    rate_limit_burst: int = _env_int("RATE_LIMIT_BURST", 30)

    # OpenRouter
    openrouter_api_url: str = _env("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
    openrouter_api_key: str = _env("OPENROUTER_API_KEY", "")
    openrouter_timeout_s: float = _env_float("OPENROUTER_TIMEOUT_S", 120.0)

    # Storage (public beta defaults)
    # - persist_storage: write conversations to disk under backend/data/ (OFF by default)
    persist_storage: bool = _env_bool("PERSIST_STORAGE", False)
    data_dir: str = _env("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
    conversations_file: str = _env("CONVERSATIONS_FILE", "")  # if empty, derived from data_dir

    # In-memory store limits
    max_conversations: int = _env_int("MAX_CONVERSATIONS", 2000)
    max_messages_per_convo: int = _env_int("MAX_MESSAGES_PER_CONVO", 200)
    prune_on_write: bool = _env_bool("PRUNE_ON_WRITE", True)

    # Logging
    log_level: str = _env("LOG_LEVEL", "INFO")

    def __post_init__(self):
        object.__setattr__(
            self,
            "cors_allow_origins",
            _parse_csv(_env("CORS_ALLOW_ORIGINS", "http://localhost,http://localhost:5173,http://127.0.0.1,http://127.0.0.1:5173")) or ["http://localhost"],
        )
        object.__setattr__(self, "cors_allow_methods", _parse_csv(_env("CORS_ALLOW_METHODS", "GET,POST,DELETE,OPTIONS")))
        object.__setattr__(
            self,
            "cors_allow_headers",
            _parse_csv(_env("CORS_ALLOW_HEADERS", "Authorization,Content-Type,X-API-Key")),
        )
        if not self.conversations_file:
            object.__setattr__(self, "conversations_file", os.path.join(self.data_dir, "conversations.json"))

        BACKCOMPAT_DATA_DIR_AUTODETECT = True
        if "DATA_DIR" not in os.environ and "CONVERSATIONS_FILE" not in os.environ:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            legacy_dir = os.path.join(project_root, "data")
            legacy_json = os.path.join(legacy_dir, "conversations.json")
            legacy_conv_dir = os.path.join(legacy_dir, "conversations")
            has_legacy = os.path.isfile(legacy_json) or (
                os.path.isdir(legacy_conv_dir)
                and any(x.endswith(".json") for x in os.listdir(legacy_conv_dir))
            )
            if has_legacy:
                object.__setattr__(self, "data_dir", legacy_dir)
                object.__setattr__(self, "conversations_file", legacy_json)
                if "PERSIST_STORAGE" not in os.environ:
                    object.__setattr__(self, "persist_storage", True)


    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_token)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return AppConfig()