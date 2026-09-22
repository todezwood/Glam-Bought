"""Environment + model configuration."""
import logging
import os

from dotenv import load_dotenv

load_dotenv()
# httpx logs every MCP request URL, which carries the Bright Data token. Keep it out of logs.
for noisy in ("httpx", "httpcore", "mcp", "strands"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "anthropic").lower()
COGNEE_SERVICE_URL = os.getenv("COGNEE_SERVICE_URL", "")
COGNEE_API_KEY = os.getenv("COGNEE_API_KEY", "")
BRIGHTDATA_API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN", "")

# Cognee datasets
DS_PROFILE = "beauty_profile"
DS_PURCHASES = "purchases"
DS_MARKET = "market"
ALL_DATASETS = [DS_PROFILE, DS_PURCHASES, DS_MARKET]


def get_model():
    """Return the Strands model for the configured provider."""
    if MODEL_PROVIDER == "bedrock":
        from strands.models import BedrockModel

        return BedrockModel(
            model_id=os.environ["BEDROCK_MODEL_ID"],
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            max_tokens=4096,
        )

    from strands.models.anthropic import AnthropicModel

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Set ANTHROPIC_API_KEY (or MODEL_PROVIDER=bedrock) in .env")
    return AnthropicModel(
        client_args={"api_key": api_key},
        model_id=os.getenv("MODEL_ID", "claude-sonnet-5"),
        max_tokens=4096,
    )
