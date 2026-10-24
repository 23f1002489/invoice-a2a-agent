"""Run in project root: python tests/real_aipipe_accuracy.py tests/invoice_gold.json
Gold fixture format: [{"package": {...}, "expected": {"action": "...", "facts": {...}, "evidenceRefs": ["[...]"]}}]
Requires AI_PIPE_TOKEN in environment or .env. NEVER print token.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.main import load_env
from app.decision_engine import ask_model, validate_decision

load_env()

def main():
    if not os.getenv("AI_PIPE_TOKEN"):
        raise SystemExit("Missing AI_PIPE_TOKEN in .env or environment")
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/invoice_gold.json")
    gold = json.loads(path.read_text(encoding="utf-8"))
    packages = [item["package"] for item in gold]
    raw = ask_model(packages, "gold-test-r1")
    # from app.decision_engine import normalize_evidence_refs
    from app.decision_engine import (normalize_evidence_refs,normalize_rationale_refs,)

    package_by_id = {
        pkg["packageId"]: pkg
        for pkg in packages
    }

    for proposal in raw:
        package_id = proposal["packageId"]

        if package_id not in package_by_id:
            raise ValueError(f"Unknown package: {package_id}")

        normalize_evidence_refs(
            proposal,
            package_by_id[package_id]
        )
        normalize_rationale_refs(proposal)

    print("\n========== RAW MODEL OUTPUT ==========")
    print(json.dumps(raw, indent=2, ensure_ascii=False))
    print("======================================\n")
    by_id = {p["packageId"]: p for p in raw}
    totals = {"action": 0, "facts": 0, "evidence_exact_set": 0, "schema": 0}
    for item in gold:
        package = item["package"]
        pid = package["packageId"]
        try:
            p = validate_decision(package, by_id[pid])
        except (ValueError, KeyError) as e:
            print(f"{pid}: INVALID PROPOSAL: {e}")
            continue
        totals["schema"] += 1
        expected = item["expected"]
        scores = {"action": p["action"] == expected["action"], "facts": p["facts"] == expected["facts"], "evidence_exact_set": set(p["evidenceRefs"]) == set(expected["evidenceRefs"])}
        for k, good in scores.items():
            totals[k] += bool(good)
        print(f"{pid}: " + " ".join(f"{k}={'PASS' if v else 'FAIL'}" for k,v in scores.items()))
    n = len(gold)
    print("\nAccuracy (gold-labeled examples):")
    for k, value in totals.items():
        print(f"  {k}: {value}/{n} ({value/n:.1%})")
    if totals["action"] / n < .75:
        raise SystemExit("Action accuracy below 75%; improve model/prompt before submission")

if __name__ == "__main__":
    main()
