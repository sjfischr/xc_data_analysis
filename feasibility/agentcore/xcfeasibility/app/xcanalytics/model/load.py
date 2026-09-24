import os
from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials."""
    return BedrockModel(model_id=os.environ.get("XC_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"))
