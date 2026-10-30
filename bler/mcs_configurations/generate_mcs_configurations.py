#!/usr/bin/env python3
"""Build the link-layer table the offline planner and the baselines read.

The output is the raw physical-layer measurement, one row per (TBS, MCS):
the per-attempt block error rate and the resource blocks that row costs.
Consumers apply Eq. (4) themselves against the exact per-TB target Eq. (3)
gives them.

The BLER is the link simulation's, unchanged: bler_core.py encodes the whole
transport block (TBEncoder at the full TBS) and counts an error when its TB
CRC fails, so the value is already the probability that one transmission of
the whole TB is not decoded. The segmentation of a packet into several TBs
is the planner's business, through Eq. (3) with the flow's own N. (An
earlier version raised the 261 B and 469 B rows to 1 - (1 - bler)^ni with
ni = 2 and 3, a fixed factor per TB size that counted segments a second time
on top of Eq. (3), whatever the packet size.)

This replaces an earlier format that pre-bucketed rows by decade error
targets (1e-2 ... 1e-8) and stored the resulting attempt count `k`. That was
only ever a lookup convenience, but it forced the planner to quantise its
Eq. (3) target onto a decade before it could look anything up -- and rounding
to the *nearest* decade relaxed the reliability target by 2-3x for the common
N = 2 and N = 3 cases. Handing out the BLER itself removes the quantisation
step entirely.
"""

import argparse
import json
import os
import re


def extract_numbers(path):
    base = os.path.basename(path)
    match = re.search(r"jakes_(\d+)_mcs_bler_results_([0-9]+(?:\.[0-9]+)?)\.json", base)
    if not match:
        raise ValueError(f"Unexpected filename format: {base}")
    return int(match.group(1)), float(match.group(2))


def main():
    parser = argparse.ArgumentParser(description="Combine MCS BLER results.")
    parser.add_argument("input_files", nargs="+", help="Input JSON files")
    parser.add_argument("--output", default="mcs_configurations.json",
                        help="Output file name")
    args = parser.parse_args()

    results = {}

    for filepath in args.input_files:
        tbs, sinr = extract_numbers(filepath)

        with open(filepath, "r") as f:
            data = json.load(f)

        rows = []
        dropped = []
        for entry in data:
            bler = entry["bler"]

            # A row that never decodes (BLER 1) carries no information for
            # Eq. (4) -- no finite number of attempts reaches any target -- and
            # a row that never fails (BLER 0) is not something the link
            # simulation can actually establish. Drop both rather than let
            # them reach the planner, where they would only be discarded again
            # one flow at a time.
            if not 0.0 < bler < 1.0:
                dropped.append(entry["mcs_index"])
                continue

            rows.append({
                "m": entry["mcs_index"],
                "bler": bler,
                "b": entry["num_rbs"],
            })
        rows.sort(key=lambda r: r["m"])

        if dropped:
            print(f"  TBS {tbs}: dropped MCS {dropped} (BLER outside (0,1) at "
                  f"{sinr} dB -- never decodes)")

        results[(tbs, sinr)] = {
            "tbs": tbs,
            "sinr": int(sinr) if sinr.is_integer() else sinr,
            "mcs": rows,
        }

    final_output = [results[k] for k in sorted(results.keys())]

    with open(args.output, "w") as f:
        json.dump(final_output, f, indent=2)

    print(f"Wrote {args.output}: {len(final_output)} (TBS, SINR) table(s)")


if __name__ == "__main__":
    main()
