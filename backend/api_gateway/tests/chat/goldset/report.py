import json
import os


def render(summary, case_results):
    lines = ["# Gold-Set Report", ""]
    lines.append(f"Cases: {summary['cases_passed']}/{summary['cases_total']} passed")
    ra = summary["routing_accuracy"]
    lines.append(
        f"Routing accuracy: {summary['routing_correct']}/{summary['routing_total']}"
        + (f" ({ra:.0%})" if ra is not None else "")
    )
    tb = summary.get("tier_b_trace_rate")
    lines.append(
        f"I5 trace presence (across all Tier B responses): "
        f"{summary.get('tier_b_with_trace', 0)}/{summary.get('tier_b_total', 0)}"
        + (f" ({tb:.0%})" if tb is not None else "")
    )
    lines.append("")
    lines.append("## Per category")
    for cat, (p, tot) in sorted(summary["by_category"].items()):
        lines.append(f"- {cat}: {p}/{tot}")
    if summary["confusion"]:
        lines.append("")
        lines.append("## Misroutes (expected -> actual)")
        for k, n in sorted(summary["confusion"].items(), key=lambda x: -x[1]):
            lines.append(f"- {k}: {n}")
    lines.append("")
    lines.append("## Failing cases")
    for cr in case_results:
        if not cr["passed"]:
            fails = [
                (_a, ok) for t in cr["turns"] for (_a, ok) in t["asserts"] if not ok
            ]
            cat = (
                cr["category"].value
                if hasattr(cr["category"], "value")
                else cr["category"]
            )
            lines.append(
                f"- [{cat}] {cr['id']}"
                + (f" — {cr['why']}" if cr["why"] else "")
                + f"  (failed asserts: {len(fails)})"
            )
    return "\n".join(lines)


def save_baseline(path, summary):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, default=str)


def load_baseline(path):
    with open(path) as f:
        return json.load(f)
