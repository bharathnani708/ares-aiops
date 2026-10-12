#!/usr/bin/env python3
"""Find which Claude models your AWS account can actually call, on both Bedrock endpoints.

    pip install "anthropic[bedrock]" boto3
    python3 scripts/probe-bedrock.py            # uses your current AWS credentials/profile

Prints a value you can paste into BEDROCK_MODEL_ID (comma-separated fallback list).
Each successful probe costs a few tokens.
"""
import os
import sys

import anthropic
import boto3
from anthropic import AnthropicBedrock, AnthropicBedrockMantle

REGION = os.getenv("AWS_REGION", "us-east-1")
models = sorted(
    m["modelId"]
    for m in boto3.client("bedrock", region_name=REGION).list_foundation_models(byProvider="anthropic")["modelSummaries"]
    if m.get("modelLifecycle", {}).get("status") == "ACTIVE"
)
mantle = AnthropicBedrockMantle(aws_region=REGION, max_retries=0)
runtime = AnthropicBedrock(aws_region=REGION, max_retries=0)
geo = REGION.split("-")[0]  # "us" for us-east-1: cross-region inference profile prefix

working = []
for model in models:
    for endpoint, client, model_id in (
        ("mantle", mantle, model),
        ("runtime", runtime, model),
        ("runtime", runtime, f"{geo}.{model}"),
    ):
        try:
            client.messages.create(model=model_id, max_tokens=16,
                                   messages=[{"role": "user", "content": "Reply with OK."}])
            status = "OK"
            working.append(f"{endpoint}:{model_id}")
        except anthropic.APIStatusError as exc:
            status = f"{exc.status_code} {str(exc.message)[:70]}"
        except Exception as exc:  # network, validation, etc.
            status = f"error {type(exc).__name__}: {str(exc)[:60]}"
        print(f"{endpoint:8} {model_id:48} {status}")
        if status == "OK":
            break  # one working route per model is enough

print()
if not working:
    sys.exit("No Claude model is callable yet. Request model access in the Bedrock console (Model catalog).")
print("Working routes, best first is up to you. Example value for BEDROCK_MODEL_ID:")
print(",".join(working[:3]))
