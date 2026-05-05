import os


REMOTE_URL: str = os.environ.get("AKC_SERVICE_REMOTE_URL", "").rstrip("/")
REMOTE_API_KEY: str = os.environ.get("AKC_SERVICE_REMOTE_API_KEY", "")
REMOTE_TIMEOUT: int = int(os.environ.get("AKC_SERVICE_REMOTE_TIMEOUT", "10"))
SYNC_ON_STARTUP: bool = os.environ.get("AKC_SERVICE_SYNC_ON_STARTUP", "false").lower() == "true"
PUSH_BATCH: int = int(os.environ.get("AKC_SERVICE_SYNC_PUSH_BATCH", "50"))

try:
    MIN_CONFIDENCE: float = float(os.environ.get("AKC_SERVICE_SYNC_MIN_CONFIDENCE", "0.70"))
except ValueError:
    MIN_CONFIDENCE = 0.70


def sync_enabled() -> bool:
    """Return True only when a remote URL is configured."""
    return bool(REMOTE_URL)
