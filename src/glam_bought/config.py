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
DS_PURCHASES = "purchases_v2"  # v1 was polluted by rehearsal approvals (fake "already chose" purchases); forget() is unreliable
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


# A small, fast model for routing each message (intent + headline for the activity card). Same
# credentials as the main model; built once so its time budget is not spent on connection setup.
_classifier_client = None


def classifier_call(system: str, user: str, timeout: float = 3.0, max_tokens: int = 200) -> str:
    """One short completion from the fast model; raises on any failure (the caller falls back)."""
    global _classifier_client
    if MODEL_PROVIDER == "bedrock":
        import boto3
        from botocore.config import Config

        if _classifier_client is None:
            _classifier_client = boto3.Session().client(
                "bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"),
                config=Config(connect_timeout=2, read_timeout=timeout, retries={"max_attempts": 0}))
        out = _classifier_client.converse(
            modelId=os.getenv("CLASSIFIER_BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            system=[{"text": system}], messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens})
        return out["output"]["message"]["content"][0]["text"]

    import anthropic

    if _classifier_client is None:
        _classifier_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = _classifier_client.with_options(timeout=timeout, max_retries=0).messages.create(
        model=os.getenv("CLASSIFIER_MODEL_ID", "claude-haiku-4-5"), max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}])
    return next((b.text for b in response.content if b.type == "text"), "")
