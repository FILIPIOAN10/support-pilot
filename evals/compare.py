"""Every saved result side by side: prints a Markdown table and writes it to evals/RESULTS.md.

uv run python -m evals.compare
"""

import json
from pathlib import Path

from evals.run import RESULTS_DIR

REPORT = Path(__file__).with_name("RESULTS.md")


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def row(record: dict) -> str:
    s = record["summary"]
    judge = s["judge"]
    graded = "-" if not judge else f"{_pct(judge['grounded'])} / {_pct(judge['honest'])} / {judge['helpful_avg']}"
    cost = "-" if s["usd_per_1000_runs"] is None else f"${s['usd_per_1000_runs']}"
    return (
        f"| {record['model']} ({record['provider']}) | {s['repeats']} | {_pct(s['tool_accuracy'])} | "
        f"{_pct(s['pass_k'])} | {_pct(s['safety'])} | {graded} | {s['latency_p50_s']} s / {s['latency_p95_s']} s | "
        f"{s['tokens_in_per_run']} + {s['tokens_out_per_run']} | {cost} |"
    )


def main() -> str:
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS_DIR.glob("*.json"))]
    table = "\n".join(
        [
            "| model | runs per case | tool accuracy | pass^k | safety | judge: grounded / honest / helpful (1-5) "
            "| latency p50 / p95 | tokens per run (in + out) | cost per 1000 runs |",
            "|---|---|---|---|---|---|---|---|---|",
            *(row(r) for r in records),
        ]
    )
    report = (
        "# Evaluation results\n\n"
        f"{table}\n\n"
        "One small dataset (`evals/cases.json`) that I wrote: a regression signal and a way to compare models, "
        "not proof of quality. Cost = average tokens per run x the price on the day of the run.\n"
    )
    REPORT.write_text(report, encoding="utf-8")
    print(table)
    return report


if __name__ == "__main__":
    main()
