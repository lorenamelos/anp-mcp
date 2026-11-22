"""Chart of the eval results: before vs after under graders v1 and v2, and Sonnet vs Haiku in v3.

    uv run python -m evals.plot_results

Reads the run files in evals/runs/ and writes evals/evals_chart.png. Every number in the
chart is computed from those files.
"""

import json
import math
from pathlib import Path

from plotly.subplots import make_subplots

HERE = Path(__file__).parent
RUNS = HERE / "runs"
OUT = HERE / "evals_chart.png"

# (grader, run before the server changes, run after them), all with claude-sonnet-5-5
BEFORE_AFTER = [
    (
        "Lenient grader (v1)",
        "20261121T141647Z_claude-sonnet-5-5",
        "20261121T193533Z_claude-sonnet-5-5",
    ),
    (
        "Strict grader (v2)",
        "20261121T225704Z_claude-sonnet-5-5_before",
        "20261121T232831Z_claude-sonnet-5-5_after",
    ),
]
# (label, run) on the same server and grader v3, different answering model
MODELS = [
    ("Claude Haiku 5.5", "20261122T000332Z_claude-haiku-5-5_haiku-v3"),
    ("Claude Sonnet 5.5", "20261121T235123Z_claude-sonnet-5-5_after-v3"),
]
GREY, BLUE, INK = "#B8BEC6", "#2F5D8C", "#1F2933"


def stats(run: str) -> dict:
    """Pass rate (%) and mean answering-model cost per answer (USD) of one run."""
    records = [json.loads(line) for line in (RUNS / f"{run}.jsonl").open(encoding="utf-8")]
    return {
        "n": len(records),
        "pass_pct": 100 * sum(r["passed"] for r in records) / len(records),
        "solver_usd": sum(r["solver_usd"] for r in records) / len(records),
    }


def main() -> None:
    fig = make_subplots(
        rows=1,
        cols=2,
        horizontal_spacing=0.14,
        column_widths=[0.5, 0.5],
        subplot_titles=(
            (
                "<b>Better tools, same model</b><br><sup>Pass rate before vs after the server "
                "changes, under two graders</sup>"
            ),
            (
                "<b>Cheaper model, same tools</b><br><sup>Pass rate vs cost per answer "
                "(strict grader v3)</sup>"
            ),
        ),
    )

    # (a) before vs after, per grader
    graders = [g for g, _, _ in BEFORE_AFTER]
    before = [stats(b)["pass_pct"] for _, b, _ in BEFORE_AFTER]
    after = [stats(a)["pass_pct"] for _, _, a in BEFORE_AFTER]
    fig.add_bar(
        x=graders,
        y=before,
        name="Before: 4 tools, no instructions",
        marker_color=GREY,
        text=[f"{v:.0f}%" for v in before],
        textposition="outside",
        row=1,
        col=1,
    )
    fig.add_bar(
        x=graders,
        y=after,
        name="After: search inside groups, national tool, instructions",
        marker_color=BLUE,
        text=[f"{v:.0f}%" for v in after],
        textposition="outside",
        row=1,
        col=1,
    )
    for g, b, a in zip(graders, before, after, strict=True):
        # Points between the rounded labels shown on the bars, so the chart reads consistently.
        gain = round(a) - round(b)
        fig.add_annotation(
            x=g,
            y=a + 12,
            text=f"<b>{gain:+d} pts</b>",
            showarrow=False,
            font={"color": BLUE, "size": 13},
            row=1,
            col=1,
        )

    # (b) pass rate vs cost per answer, per answering model
    models = [(label, stats(run)) for label, run in MODELS]
    costs = [s["solver_usd"] for _, s in models]
    rates = [s["pass_pct"] for _, s in models]
    fig.add_scatter(
        x=costs,
        y=rates,
        mode="markers+text",
        marker={"size": 16, "color": [GREY, BLUE], "line": {"width": 1, "color": INK}},
        text=[f"{label}<br>{s['pass_pct']:.0f}% · ${s['solver_usd']:.4f}" for label, s in models],
        textposition=["top right", "top left"],
        showlegend=False,
        row=1,
        col=2,
    )
    cheap, dear = models[0][1], models[1][1]
    ratio = dear["solver_usd"] / cheap["solver_usd"]
    drop = round(dear["pass_pct"]) - round(cheap["pass_pct"])
    fig.add_annotation(
        x=math.log10(math.sqrt(cheap["solver_usd"] * dear["solver_usd"])),  # midpoint on log axis
        y=min(rates) - 2.8,
        xref="x2",
        yref="y2",
        text=f"{ratio:.0f}× cheaper per answer, {drop} pts lower",
        showarrow=False,
        font={"size": 12, "color": INK},
    )

    answers = models[0][1]["n"]
    fig.update_yaxes(
        range=[0, 112], ticksuffix="%", title_text=f"Pass rate ({answers} answers)", row=1, col=1
    )
    fig.update_yaxes(
        range=[min(rates) - 7, max(rates) + 4],
        ticksuffix="%",
        title_text=f"Pass rate ({answers} answers)",
        row=1,
        col=2,
    )
    ticks = [0.0005, 0.001, 0.002, 0.005, 0.01, 0.02]
    fig.update_xaxes(
        type="log",
        range=[-3.6, -1.6],
        tickvals=ticks,
        ticktext=[f"${t:g}" for t in ticks],
        title_text="Cost per answer, answering model only (log scale)",
        row=1,
        col=2,
    )
    fig.update_layout(
        template="simple_white",
        barmode="group",
        height=470,
        width=1150,
        font={"family": "Inter, Helvetica, Arial", "color": INK, "size": 12},
        legend={"orientation": "h", "y": -0.22, "x": 0},
        margin={"t": 110, "b": 95, "l": 70, "r": 30},
        title={
            "text": "anp-mcp evals: 21 questions × 3 repeats, graded against answers "
            "computed from the dataset",
            "x": 0.01,
            "font": {"size": 15},
        },
    )
    fig.write_image(OUT, scale=2)
    print(f"Chart: {OUT.relative_to(HERE.parent)}")
    for g, b, a in zip(graders, before, after, strict=True):
        print(f"{g}: {b:.1f}% -> {a:.1f}% ({a - b:+.1f} pts)")
    for label, s in models:
        print(f"{label}: {s['pass_pct']:.1f}%, ${s['solver_usd']:.5f} per answer")


if __name__ == "__main__":
    main()
