"""Environment + model configuration."""
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()
# httpx logs every MCP request URL, which carries the Bright Data token. Keep it out of logs.
for noisy in ("httpx", "httpcore", "mcp", "strands"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "anthropic").lower()
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "")  # read at import: cognee re-loads .env later with override
COGNEE_SERVICE_URL = os.getenv("COGNEE_SERVICE_URL", "")
COGNEE_API_KEY = os.getenv("COGNEE_API_KEY", "")
BRIGHTDATA_API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN", "")

# Oura ring + Google Calendar (OAuth2; tokens live in tokens/<provider>.json)
OURA_CLIENT_ID = os.getenv("OURA_CLIENT_ID", "")
OURA_CLIENT_SECRET = os.getenv("OURA_CLIENT_SECRET", "")
OURA_USE_SANDBOX = os.getenv("OURA_USE_SANDBOX", "").lower() in ("1", "true", "yes")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
OAUTH_BASE_URL = os.getenv("OAUTH_BASE_URL", "http://localhost:8787").rstrip("/")
LOCAL_TZ = os.getenv("LOCAL_TZ", "America/Los_Angeles")

ROOT = Path(__file__).resolve().parent.parent.parent
TOKENS_DIR = ROOT / "tokens"
SEED_DIR = ROOT / "seed"

# Cognee datasets
DS_PROFILE = "beauty_profile_v3"  # v1: placeholder persona; v2: Angela in San Francisco; v3: Angela in Taiwan (Cognee forget() is unreliable, so old versions are simply no longer recalled)
DS_PURCHASES = "purchases"
DS_MARKET = "market_v2"  # v1 holds ~220 shell rows from the first Sephora seed; Cognee Cloud forget() timed out, so it is simply no longer recalled
DS_WELLNESS = "wellness"
ALL_DATASETS = [DS_PROFILE, DS_PURCHASES, DS_MARKET, DS_WELLNESS]


def tz() -> ZoneInfo:
    return ZoneInfo(LOCAL_TZ)


def local_now() -> datetime:
    return datetime.now(tz()).replace(microsecond=0)


def today_line() -> str:
    """'Today is Monday 2026-09-21, 6:34 PM America/Los_Angeles (PDT).' Injected every turn."""
    n = local_now()
    return f"Today is {n:%A %Y-%m-%d}, {n:%-I:%M %p} {LOCAL_TZ} ({n:%Z})."


def get_model():
    """Return the Strands model for the configured provider."""
    if MODEL_PROVIDER == "bedrock":
        from strands.models import BedrockModel

        return BedrockModel(
            model_id=BEDROCK_MODEL_ID or os.environ["BEDROCK_MODEL_ID"],
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            max_tokens=4096,
            streaming=False,  # ConverseStream is gated on the Anthropic use-case form; Converse is open
        )

    from strands.models.anthropic import AnthropicModel

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Set ANTHROPIC_API_KEY (or MODEL_PROVIDER=bedrock) in .env")
    return AnthropicModel(
        client_args={"api_key": api_key},
        model_id=os.getenv("MODEL_ID", "claude-sonnet-5"),
        max_tokens=8192,  # a basket turn with three candidates overran 4096 (tool input + prose + cards)
    )
