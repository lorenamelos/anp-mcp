"""Summarise one or more eval runs: pass rate by case and category, error types, cost.

    uv run python -m evals.report evals/results/<run>.jsonl [more runs...]

Also writes a .csv next to the first run file (one row per answer), to open in Excel or
Google Sheets.
"""

import sys
from pathlib import Path

import pandas as pd

CSV_COLUMNS = ["model", "case", "category", "repeat", "question", "passed", "tool_ok",
               "error_type", "reason", "tools_called", "answer", "solver_usd", "judge_usd",
               "seconds", "rubric"]


def main(paths: list[str]) -> None:
    df = pd.concat([pd.read_json(p, lines=True) for p in paths], ignore_index=True)
    df["usd"] = df["solver_usd"] + df["judge_usd"]
    df["tools_called"] = df["calls"].apply(lambda cs: ", ".join(c["name"] for c in cs))
    csv = Path(paths[0]).with_suffix(".csv")
    df[CSV_COLUMNS].to_csv(csv, index=False, encoding="utf-8-sig")  # utf-8-sig: accents in Excel
    print(f"Table for Excel/Sheets: {csv}")
    for model, d in df.groupby("model"):
        print(f"\n=== {model}  (judge: {d['judge_model'].iloc[0]}, dataset {d['dataset_revision'].iloc[0]})")
        print(f"Pass rate: {d['passed'].mean():.0%}  ({d['passed'].sum()}/{len(d)} runs)")
        print(f"Cost: ${d['usd'].sum():.4f} total, ${d['solver_usd'].mean():.4f} per answer "
              f"(solver), {d['seconds'].mean():.1f} s per answer")
        print("\nBy category:")
        print(d.groupby("category")["passed"].agg(["mean", "count"]).round(2).to_string())
        print("\nBy case (pass rate over repeats):")
        by_case = d.groupby("case").agg(passed=("passed", "mean"), question=("question", "first"))
        print(by_case.round(2).to_string())
        fails = d[~d["passed"]]
        if len(fails):
            print("\nError types in failed runs:")
            print(fails["error_type"].value_counts().to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
