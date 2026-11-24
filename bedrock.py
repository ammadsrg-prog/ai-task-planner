"""The ONLY file that talks to Bedrock. Every AI call goes through here, so every call gets its cost logged."""

import json
import os
from datetime import datetime

import boto3
from dotenv import load_dotenv

import governance
from storage import DATA_DIR

load_dotenv()
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "mistral.mistral-large-3-675b-instruct")

# Price in US dollars per 1 million tokens (Mistral Large 3, on-demand, us-east-1).
# Check https://aws.amazon.com/bedrock/pricing/ if you change the model.
PRICE_INPUT = float(os.getenv("PRICE_PER_MILLION_INPUT", "0.50"))
PRICE_OUTPUT = float(os.getenv("PRICE_PER_MILLION_OUTPUT", "1.50"))

COSTS_FILE = DATA_DIR / "costs.jsonl"


class BudgetExceededError(Exception):
    """Raised when today's AI spending has reached the daily limit."""


def call_bedrock(feature, **request):
    """Send a request to Bedrock, write its cost to costs.jsonl, and return the response.
    `feature` says what the call was for (like "chat" or "goal_plan"), so we can see what costs most."""
    # Governance: the daily budget limit. Checked BEFORE the call, so a bug can never run up a big bill.
    spent_today = cost_summary()["today_usd"]
    if spent_today >= governance.DAILY_BUDGET_USD:
        governance.log_event("budget_blocked", feature=feature, spent_today=round(spent_today, 4),
                             limit=governance.DAILY_BUDGET_USD)
        raise BudgetExceededError(
            f"Daily AI budget reached (${spent_today:.2f} of ${governance.DAILY_BUDGET_USD:.2f}). "
            "AI features are paused until tomorrow."
        )

    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    response = client.converse(modelId=MODEL_ID, **request)

    usage = response.get("usage", {})
    input_tokens = usage.get("inputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)
    cost = input_tokens / 1_000_000 * PRICE_INPUT + output_tokens / 1_000_000 * PRICE_OUTPUT

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "feature": feature,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 6),
    }
    with COSTS_FILE.open("a", encoding="utf-8") as f:  # "a" = append: add a line, keep the old ones
        f.write(json.dumps(record) + "\n")
    return response


def load_costs():
    if not COSTS_FILE.exists():
        return []
    lines = COSTS_FILE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def cost_summary():
    """Totals for the Costs page: all time, today, and per feature."""
    records = load_costs()
    today = datetime.now().date().isoformat()
    by_feature = {}
    for r in records:
        f = by_feature.setdefault(r["feature"], {"calls": 0, "cost_usd": 0.0})
        f["calls"] += 1
        f["cost_usd"] += r["cost_usd"]
    return {
        "calls": len(records),
        "total_usd": sum(r["cost_usd"] for r in records),
        "today_usd": sum(r["cost_usd"] for r in records if r["time"].startswith(today)),
        "tokens": sum(r["input_tokens"] + r["output_tokens"] for r in records),
        "by_feature": by_feature,
    }
