"""Extraction evaluation script.

Usage:
    GEMINI_API_KEY=<key> python scripts/eval_extraction.py

Loops over samples/*.pdf, compares extraction output against hand-labelled
ground truth in samples/labels/<stem>.json, and prints a per-field accuracy
table plus an overall critical-field accuracy.  Results are also written to
eval_results.csv.

Ground-truth JSON format (minimal):
{
  "sanctioned_amount_inr": 20000,
  "term_months": 24,
  "interest_rate_pct": 15.0,
  "instalment_amount_inr": 969.73,
  "stated_apr_pct": 17.07,
  "charges": [
    { "name": "Processing fee", "amount_inr": 240 },
    { "name": "Third-party fee", "amount_inr": 160 }
  ]
}

Critical fields (per PRD §16):
  sanctioned_amount_inr, term_months, interest_rate_pct,
  instalment_amount_inr, stated_apr_pct,
  each charge (name + amount)
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services.extractor import extract, ExtractionResult

_SAMPLES_DIR = Path(__file__).parent.parent / "samples"
_LABELS_DIR = _SAMPLES_DIR / "labels"
_OUT_CSV = Path(__file__).parent.parent / "eval_results.csv"

_NUMERIC_TOL = 0.02  # 2% relative tolerance for numeric comparisons

_CRITICAL_FIELDS = [
    "sanctioned_amount_inr",
    "term_months",
    "interest_rate_pct",
    "instalment_amount_inr",
    "stated_apr_pct",
]


# ---------------------------------------------------------------------------
# Field extractors from ExtractionResult
# ---------------------------------------------------------------------------


def _get_extracted(result: ExtractionResult) -> dict:
    """Pull critical scalar values out of the Pydantic extraction object."""
    if not result.success or not result.extraction:
        return {}
    ext = result.extraction
    out: dict = {}

    out["sanctioned_amount_inr"] = ext.loan.sanctioned_amount_inr.value
    out["term_months"] = ext.loan.term_months.value
    out["interest_rate_pct"] = ext.rate.interest_rate_pct.value
    out["stated_apr_pct"] = ext.stated_apr_pct.value

    if ext.loan.instalments:
        out["instalment_amount_inr"] = ext.loan.instalments[0].amount_inr.value
    else:
        out["instalment_amount_inr"] = None

    out["charges"] = [
        {"name": c.name, "amount_inr": c.amount_inr}
        for c in ext.charges
    ]
    return out


def _numeric_match(extracted, ground_truth) -> bool:
    if extracted is None or ground_truth is None:
        return False
    if ground_truth == 0:
        return extracted == 0
    return abs(extracted - ground_truth) / abs(ground_truth) <= _NUMERIC_TOL


def _charges_accuracy(extracted_charges: list, gt_charges: list) -> tuple[int, int]:
    """Return (matched, total_gt) for charge name+amount pairs."""
    if not gt_charges:
        return 0, 0
    matched = 0
    for gt in gt_charges:
        gt_name = gt.get("name", "").lower()
        gt_amt = gt.get("amount_inr")
        for ext in extracted_charges:
            ext_name = (ext.get("name") or "").lower()
            ext_amt = ext.get("amount_inr")
            name_ok = gt_name in ext_name or ext_name in gt_name
            amt_ok = _numeric_match(ext_amt, gt_amt)
            if name_ok and amt_ok:
                matched += 1
                break
    return matched, len(gt_charges)


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------


def evaluate() -> None:
    pdfs = sorted(_SAMPLES_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {_SAMPLES_DIR}. Add sample PDFs to run eval.")
        return

    rows: list[dict] = []
    field_totals: dict[str, list[int]] = {f: [0, 0] for f in _CRITICAL_FIELDS}
    charge_totals = [0, 0]

    for pdf_path in pdfs:
        stem = pdf_path.stem
        label_path = _LABELS_DIR / f"{stem}.json"
        if not label_path.exists():
            print(f"[SKIP] {pdf_path.name} — no label file at {label_path}")
            continue

        gt = json.loads(label_path.read_text(encoding="utf-8"))
        file_bytes = pdf_path.read_bytes()
        mime_type = "application/pdf"

        print(f"\n[EVAL] {pdf_path.name} ...", flush=True)
        result = extract(file_bytes, mime_type)
        extracted = _get_extracted(result)

        row: dict = {"file": pdf_path.name, "extraction_success": result.success}

        for fld in _CRITICAL_FIELDS:
            gt_val = gt.get(fld)
            ext_val = extracted.get(fld)
            match = _numeric_match(ext_val, gt_val) if isinstance(gt_val, (int, float)) else (ext_val == gt_val)
            row[fld + "_gt"] = gt_val
            row[fld + "_extracted"] = ext_val
            row[fld + "_match"] = int(match)
            field_totals[fld][0] += int(match)
            field_totals[fld][1] += 1

        gt_charges = gt.get("charges", [])
        ext_charges = extracted.get("charges", [])
        c_match, c_total = _charges_accuracy(ext_charges, gt_charges)
        row["charges_matched"] = c_match
        row["charges_total_gt"] = c_total
        charge_totals[0] += c_match
        charge_totals[1] += c_total

        rows.append(row)

    if not rows:
        print("No labelled PDFs evaluated.")
        return

    # ---- print summary table ----
    print("\n" + "=" * 72)
    print(f"{'Field':<35} {'Correct':>7} {'Total':>5} {'Accuracy':>9}")
    print("-" * 72)
    overall_correct, overall_total = 0, 0
    for fld in _CRITICAL_FIELDS:
        correct, total = field_totals[fld]
        acc = correct / total if total else float("nan")
        print(f"{fld:<35} {correct:>7} {total:>5} {acc:>8.1%}")
        overall_correct += correct
        overall_total += total

    c_correct, c_total = charge_totals
    c_acc = c_correct / c_total if c_total else float("nan")
    print(f"{'charges (name+amount)':<35} {c_correct:>7} {c_total:>5} {c_acc:>8.1%}")
    overall_correct += c_correct
    overall_total += c_total

    print("-" * 72)
    oa = overall_correct / overall_total if overall_total else float("nan")
    print(f"{'OVERALL CRITICAL FIELDS':<35} {overall_correct:>7} {overall_total:>5} {oa:>8.1%}")
    print("=" * 72)

    # ---- write CSV ----
    if rows:
        fieldnames = list(rows[0].keys())
        with _OUT_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nResults written to {_OUT_CSV}")


if __name__ == "__main__":
    evaluate()
