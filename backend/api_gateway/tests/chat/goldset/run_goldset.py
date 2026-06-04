import sys
from goldset.harness import login, run_case
from goldset.dataset import CASES
from goldset.scoring import aggregate
from goldset.report import render, save_baseline


def main(baseline_path=None):
    token = login()
    results = []
    for c in CASES:
        try:
            results.append(run_case(token, c))
        except Exception as e:
            results.append(
                {
                    "id": c.id,
                    "category": c.category,
                    "why": c.why,
                    "passed": False,
                    "turns": [],
                    "error": str(e),
                }
            )
            print(f"ERROR {c.id}: {e}", file=sys.stderr)
    summary = aggregate(results)
    print(render(summary, results))
    if baseline_path:
        save_baseline(baseline_path, summary)
        print(f"\nBaseline saved -> {baseline_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
