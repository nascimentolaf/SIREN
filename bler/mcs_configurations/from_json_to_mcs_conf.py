#!/usr/bin/env python3

import json
import argparse
import re
import os

TARGETS = [10 ** (-i) for i in range(2, 9)]
MAX_K = 4


def extract_numbers(path):
    base = os.path.basename(path)

    match = re.search(
        r"jakes_(\d+)_mcs_bler_results_([0-9]+(?:\.[0-9]+)?)\.json",
        base
    )

    if not match:
        raise ValueError(f"Unexpected filename format: {base}")

    tbs = match.group(1)
    sinr = match.group(2)

    # normalize sinr (optional but recommended)
    if sinr.endswith(".0"):
        sinr = sinr[:-2]

    return tbs, sinr


def find_valid_pair(bler, target):
    prob = bler
    for k in range(1, MAX_K + 1):
        if prob <= target:
            return k
        prob *= bler
    return None


def load_data(path):
    with open(path, "r") as f:
        return json.load(f)


def build_table(data, tbs):
    table = {}

    # The simulated BLER is already that of the whole TB; segmentation is
    # Eq. (3)'s business. (A factor ni = 3 / 2 for 469 / 261 B used to sit
    # here, but tbs is a string, so it never applied.)
    for entry in data:
        mcs = entry["mcs_index"]
        bler = entry["bler"]
        row = {}
        for t in TARGETS:
            row[t] = find_valid_pair(bler, t)

        table[mcs] = row

    return table


def to_latex(table, tbs, sinr):
    header = " & ".join([f"$10^{{-{i}}}$" for i in range(2, 9)]) + " \\\\ \\hline"

    rows = []
    for mcs in sorted(table.keys()):
        mpt = 0
        row = []
        for t in TARGETS:
            k = table[mcs][t]
            row.append("--" if k is None else f"({mcs},{k})")
            if row[-1] == "--":
                mpt += 1
        if mpt < 7:
         rows.append(" & ".join(row) + " \\\\")

    return "\n".join([
        "\\begin{table*}[ht]",
        "\\centering",
        "\\caption{(m,k) configurations for TBS=" + str(tbs) + "B, SINR=" + str(sinr) + "dB}",
        "\\label{tab:conf_{" + str(tbs) + "B_" + str(sinr) + "dB}}",
        "\\begin{tabular}{" + "c" * len(TARGETS) + "}",
        "\\hline",
        header,
        *rows,
        "\\hline",
        "\\end{tabular}",
        "\\end{table*}"
    ])

def filter_dominated(table):
    """
    For each target column, keep only the entry with the maximum m (MCS index)
    for each k value. All lower-m entries with the same k become None.
    """
    filtered = {mcs: dict(row) for mcs, row in table.items()}

    for t in TARGETS:
        # Group MCS indices by their k value for this target
        k_to_mcs = {}
        for mcs, row in table.items():
            k = row[t]
            if k is None:
                continue
            if k not in k_to_mcs:
                k_to_mcs[k] = []
            k_to_mcs[k].append(mcs)

        # For each k, null out all but the maximum MCS
        for k, mcs_list in k_to_mcs.items():
            max_mcs = max(mcs_list)
            for mcs in mcs_list:
                if mcs != max_mcs:
                    filtered[mcs][t] = None

    return filtered

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file")

    args = parser.parse_args()

    tbs, sinr = extract_numbers(args.json_file)

    out_base = f"mk_conf_{tbs}B_{sinr}dB"

    data = load_data(args.json_file)
    table = build_table(data, tbs)
    table = filter_dominated(table)


    # Python output
    with open(out_base + ".py", "w") as f:
        f.write("results = ")
        f.write(json.dumps(table, indent=2))

    # LaTeX output
    with open(out_base + ".tex", "w") as f:
        f.write(to_latex(table, tbs, sinr))

    print(f"Written:")
    print(f"  {out_base}.py")
    print(f"  {out_base}.tex")


if __name__ == "__main__":
    main()