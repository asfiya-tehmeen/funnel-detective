"""Regenerate the synthetic dataset and the hidden ground-truth file.

Usage (from backend/):
    python -m scripts.generate_data [--seed 20260720]
"""
from __future__ import annotations

import argparse
import time

from app.config import BACKEND_DIR, EVENTS_PATH
from app.data.generator import SEED, write_dataset

TRUTH_PATH = BACKEND_DIR.parent / "eval" / "ground_truth.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    t0 = time.perf_counter()
    df = write_dataset(EVENTS_PATH, TRUTH_PATH, seed=args.seed)
    print(f"Wrote {len(df):,} events ({df['session_id'].nunique():,} sessions) to {EVENTS_PATH}")
    print(f"Wrote ground truth to {TRUTH_PATH}")
    print(f"Done in {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
