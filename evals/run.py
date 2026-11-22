"""Evaluate the model from the settings and save the result as evals/results/<label>.json.

    uv run python -m evals.run                                  # scripted model, 1 run per case: free and offline
    $env:LLM_PROVIDER="openai"; uv run python -m evals.run --repeats 3 --price-in 0.40 --price-out 1.60 --judge openai
    $env:LLM_PROVIDER="bedrock"; uv run python -m evals.run --repeats 3 --price-in 0.06 --price-out 0.24 --judge openai

Prices are USD per 1M tokens: copy them from the provider's pricing page on the day you run (they change).
`uv run python -m evals.compare` then puts every saved result in one table.
"""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from evals.harness import evaluate, load_cases, make_judge, model_name, summarize

from supportpilot.config import get_settings
from supportpilot.llm import make_chat_model

RESULTS_DIR = Path(__file__).with_name("results")


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Evaluate the SupportPilot agent against one model.")
    parser.add_argument("--repeats", type=int, default=1, help="runs per case (pass^k needs k > 1)")
    parser.add_argument("--label", help="name of the result file (default: provider-model)")
    parser.add_argument("--price-in", type=float, help="USD per 1M input tokens")
    parser.add_argument("--price-out", type=float, help="USD per 1M output tokens")
    parser.add_argument("--judge", choices=["openai", "bedrock"], help="grade the replies with this provider")
    parser.add_argument("--judge-model", help="the judge's model (default: CHAT_MODEL / BEDROCK_MODEL_ID)")
    args = parser.parse_args(argv)

    settings = get_settings()
    model = make_chat_model(settings)
    name = model_name(model)
    judge = None
    if args.judge:
        update = {"chat_model": args.judge_model, "bedrock_model_id": args.judge_model} if args.judge_model else {}
        judge = make_judge(make_chat_model(settings.model_copy(update=update), args.judge))
    print(f"model: {settings.llm_provider} / {name}   runs per case: {args.repeats}   judge: {args.judge or 'none'}\n")

    results = evaluate(model, settings, load_cases(), repeats=args.repeats, judge=judge)
    summary = summarize(results, args.price_in, args.price_out)

    label = args.label or f"{settings.llm_provider}-{name}".replace(":", "_").replace("/", "_")
    record = {
        "label": label,
        "provider": settings.llm_provider,
        "model": name,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": summary,
        "cases": [asdict(r) for r in results],
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{label}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "\n".join(f"{key:20} {value}" for key, value in summary.items()))
    print(f"\nsaved: {path}")
    return record


if __name__ == "__main__":
    main()
