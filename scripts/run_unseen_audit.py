
import json
import os
import sys
from pathlib import Path

# Allow execution from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(
    dotenv_path=Path(__file__).resolve().parents[1] / ".env"
)

from app.decision_engine import (
    ask_model,
    normalize_evidence_refs,
    normalize_rationale_refs,
    validate_decision,
)

CASES = [
    {
        "action": "settle_invoice",
        "packageId": "audit-regular",
        "content": (
            "Current operative decision: Meridian Office invoiced "
            "INV-M731 for 7300 INR. [A-731] Goods receipt and vendor "
            "identity both reconcile. [B-731] This amount is within "
            "delegated settlement authority and has no payment "
            "history. [C-731]"
        ),
    },
    {
        "action": "request_approval",
        "packageId": "audit-approval",
        "content": (
            "Operative review: Atlas Freight invoice INV-F208 for "
            "810000 INR matches delivery evidence. [A-208] Commercial "
            "verification is complete but this amount exceeds the "
            "approver-free limit. [B-208] Finance director "
            "authorization is mandatory before settlement. [C-208]"
        ),
    },
    {
        "action": "hold_invoice",
        "packageId": "audit-hold",
        "content": (
            "Operative review: Cedar Components invoice INV-C405 for "
            "19000 INR was received. [A-405] The bank account change "
            "is awaiting independent callback verification. [B-405] "
            "Hold payment until verification is complete; no rejection "
            "or approval escalation has been ordered. [C-405]"
        ),
    },
    {
        "action": "reject_duplicate",
        "packageId": "audit-duplicate",
        "content": (
            "Operative review: Northstar Services invoice INV-N552 "
            "for 44000 INR matches a settled ledger entry. [A-552] "
            "The exact same invoice number and amount were paid "
            "yesterday, transaction PAY-552. [B-552] Reject this "
            "duplicate to avoid paying twice. [C-552]"
        ),
    },
    {
        "action": "open_exception",
        "packageId": "audit-conflict",
        "content": (
            "Operative review: Delta Equipment invoice INV-D914 "
            "for 152000 INR is recorded by procurement. [A-914] "
            "The signed purchase order specifies 125000 INR but "
            "the supplier's signed invoice specifies 152000 INR "
            "for the same items. [B-914] These materially conflicting "
            "records cannot be reconciled; open an exception "
            "for investigation. [C-914]"
        ),
    },
]


def main():
    packages = [
        {
            "packageId": case["packageId"],
            "documents": [{"content": case["content"]}],
        }
        for case in CASES
    ]

    print("Calling AI Pipe for five unseen audit scenarios...")
    raw = ask_model(packages, "unseen-audit-r1")

    if not isinstance(raw, list):
        raise ValueError("Model did not return a list")

    by_id = {
        p["packageId"]: p
        for p in raw
        if isinstance(p, dict) and isinstance(p.get("packageId"), str)
    }

    passed = 0

    for case, package in zip(CASES, packages):
        pid = package["packageId"]
        expected = case["action"]

        try:
            proposal = by_id[pid]
            normalize_evidence_refs(proposal, package)
            normalize_rationale_refs(proposal)

            validated = validate_decision(package, proposal)
            actual = validated["action"]
            ok = actual == expected
            passed += int(ok)

            print(
                f"{pid}: {'PASS' if ok else 'FAIL'} "
                f"expected={expected} actual={actual}"
            )

        except (KeyError, ValueError, TypeError) as exc:
            print(f"{pid}: INVALID - {exc}")

    total = len(CASES)
    print(f"\nUnseen audit: {passed}/{total} ({passed / total:.0%})")

    if len(raw) != total or len(by_id) != total or passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
