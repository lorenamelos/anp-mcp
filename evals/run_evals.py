"""Run the anp-mcp evals: an MCP client that plays the role of Claude Desktop.

For each case in cases.yaml it starts a conversation with Claude, gives it the tools that the
anp-mcp server lists (over stdio, exactly as a desktop client sees them), executes the tool
calls on the server, and then asks a grader model to check the final answer against the
rubric filled with ground_truth.py. Every API call's tokens and cost are recorded.

    uv run python -m evals.run_evals                      # all cases, 3 repeats, Sonnet
    uv run python -m evals.run_evals --cases q05 q21 -r 1 # a few cases, once
    uv run python -m evals.run_evals --model claude-haiku-5-5

Needs ANTHROPIC_API_KEY in .env (never committed). Results: evals/results/<run>.jsonl
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from evals.ground_truth import compute

logging.getLogger("httpx").setLevel(logging.WARNING)  # no per-request log lines

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
MAX_TURNS = 8  # tool-use rounds per question before giving up
SYSTEM = (
    "You are a helpful assistant. You have tools for Brazilian oil production data "
    "(ANP). Use them when they are relevant to the question."
)
GRADE_TOOL = {
    "name": "grade",
    "description": "Record the grade of the assistant's answer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean", "description": "True only if the rubric is fully met."},
            "reason": {"type": "string", "description": "One or two sentences on why."},
            "error_type": {
                "type": "string",
                "enum": ["none", "wrong_number", "wrong_unit", "wrong_entity", "invented",
                         "missed_limit", "incomplete", "other"],
            },
        },
        "required": ["passed", "reason", "error_type"],
    },
}
GRADER_PROMPT = """You grade answers from an assistant that uses data tools.

Question: {question}

Rubric (what a correct answer must do): {rubric}

Tool calls the assistant made (name, arguments, start of the result):
{trace}

Assistant's final answer:
{answer}

Grade strictly against the rubric. Numbers may be rounded or formatted differently
(e.g. "1,2 milhão" = 1,200,000) but must be within the stated tolerance.
You must answer by calling the grade tool, with no other text."""


class Costs:
    """Tokens and USD per model, from each response's usage."""

    def __init__(self, pricing: dict):
        self.pricing = pricing
        self.by_model: dict[str, dict] = {}

    def add(self, model: str, usage) -> float:
        p = self.pricing[model]
        tokens = {
            "input": usage.input_tokens or 0,
            "output": usage.output_tokens or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        usd = sum(tokens[k] * p[k] for k in tokens) / 1e6
        acc = self.by_model.setdefault(model, {**dict.fromkeys(tokens, 0), "usd": 0.0})
        for k, v in tokens.items():
            acc[k] += v
        acc["usd"] += usd
        return usd

    @property
    def total(self) -> float:
        return sum(m["usd"] for m in self.by_model.values())


def result_text(result) -> str:
    parts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    return "\n".join(parts) or json.dumps(result.structured_content or {})


async def ask(client, session, tools, model, system, question, costs) -> dict:
    """One conversation: question -> tool calls -> final answer."""
    messages = [{"role": "user", "content": question}]
    calls, usd, start = [], 0.0, time.monotonic()
    for _ in range(MAX_TURNS):
        resp = await client.messages.create(
            model=model, max_tokens=2048, system=system, tools=tools, messages=messages
        )
        usd += costs.add(model, resp.usage)
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            answer = "\n".join(b.text for b in resp.content if b.type == "text")
            break
        results = []
        for u in uses:
            res = await session.call_tool(u.name, u.input)
            text = result_text(res)
            calls.append({"name": u.name, "args": u.input, "result": text[:500],
                          "is_error": bool(res.is_error)})
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": text,
                            "is_error": bool(res.is_error)})
        messages.append({"role": "user", "content": results})
    else:
        answer = "(no final answer: tool-use turn limit reached)"
    return {"answer": answer, "calls": calls, "solver_usd": usd,
            "seconds": round(time.monotonic() - start, 1)}


async def grade(client, judge_model, case, rubric, run, costs) -> dict:
    trace = "\n".join(
        f"- {c['name']}({json.dumps(c['args'], ensure_ascii=False)}) -> {c['result'][:300]}"
        for c in run["calls"]
    ) or "(no tool calls)"
    resp = await client.messages.create(
        # Forcing the tool (tool_choice "tool"/"any") is not supported by the 5.5 models, so
        # the prompt asks for it and a missing call counts as a grading error.
        model=judge_model, max_tokens=1024, tools=[GRADE_TOOL],
        messages=[{"role": "user", "content": GRADER_PROMPT.format(
            question=case["question"], rubric=rubric, trace=trace, answer=run["answer"])}],
    )
    usd = costs.add(judge_model, resp.usage)
    verdict = next((b.input for b in resp.content if b.type == "tool_use"), None)
    if verdict is None:
        text = " ".join(b.text for b in resp.content if b.type == "text")
        verdict = {"passed": False, "reason": f"GRADER DID NOT CALL grade: {text[:200]}",
                   "error_type": "other"}
    return {**verdict, "judge_usd": usd}


async def main(args) -> None:
    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY missing: add it to .env at the repository root.")
    pricing = yaml.safe_load((HERE / "pricing.yaml").read_text())
    for m in (args.model, args.judge_model):
        if m not in pricing:
            sys.exit(f"No price for {m} in evals/pricing.yaml.")
    cases = yaml.safe_load((HERE / "cases.yaml").read_text())
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases]
    truth = compute()
    costs = Costs(pricing)
    client = AsyncAnthropic()

    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{args.model}"
    out = HERE / "results" / f"{run_id}.jsonl"
    out.parent.mkdir(exist_ok=True)

    # The server runs as a child process, exactly as Claude Desktop would start it.
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "anp_mcp.server"], env=dict(os.environ),
        cwd=str(REPO_ROOT),
    )
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        # Like desktop clients, pass the server's own instructions (if any) to the model.
        # A server that sends none (the baseline) gets exactly the old system prompt.
        server_instructions = getattr(init, "instructions", None) or ""
        system = SYSTEM
        if server_instructions:
            system += f"\n\n<server_instructions name=\"anp-mcp\">\n{server_instructions}\n</server_instructions>"
        print(f"Server instructions: {'yes' if server_instructions else 'none'}")
        listed = await session.list_tools()
        tools = [{"name": t.name, "description": t.description or "",
                  "input_schema": t.input_schema} for t in listed.tools]
        print(f"{len(tools)} tools: {', '.join(t['name'] for t in tools)}")

        with out.open("w", encoding="utf-8") as f:
            for case in cases:
                rubric = case["rubric"].format(**truth)
                for rep in range(args.repeats):
                    if costs.total >= args.max_usd:
                        print(f"Stopped: budget of ${args.max_usd:.2f} reached.")
                        break
                    run = await ask(client, session, tools, args.model, system, case["question"], costs)
                    verdict = await grade(client, args.judge_model, case, rubric, run, costs)
                    called = {c["name"] for c in run["calls"]}
                    tool_ok = not case["expect_tools"] or bool(called & set(case["expect_tools"]))
                    passed = verdict["passed"] and tool_ok
                    record = {
                        "run_id": run_id, "case": case["id"], "category": case["category"],
                        "repeat": rep, "model": args.model, "judge_model": args.judge_model,
                        "dataset_revision": truth["dataset_revision"],
                        "server_instructions": bool(server_instructions),
                        "tools_available": [t["name"] for t in tools],
                        "question": case["question"], "rubric": rubric, **run,
                        "tool_ok": tool_ok, **verdict, "passed": passed,
                    }
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    mark = "PASS" if passed else "FAIL"
                    print(f"{case['id']} r{rep} {mark:4} {verdict['error_type']:12} "
                          f"${run['solver_usd'] + verdict['judge_usd']:.4f}  {verdict['reason'][:90]}")

    print(f"\nCost by model: {json.dumps(costs.by_model, indent=1)}")
    print(f"Total: ${costs.total:.4f}   Results: {out.relative_to(REPO_ROOT)}")
    print("Summary: uv run python -m evals.report " + str(out.relative_to(REPO_ROOT)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--judge-model", default="claude-sonnet-5-5")
    parser.add_argument("-r", "--repeats", type=int, default=3)
    parser.add_argument("--cases", nargs="*", help="case ids, e.g. q01 q05")
    parser.add_argument("--max-usd", type=float, default=2.0, help="stop the run at this cost")
    asyncio.run(main(parser.parse_args()))
