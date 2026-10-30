import json
import sys
import os
import matplotlib.pyplot as plt
import numpy as np

def load_data(filepath):
    with open(filepath, "r") as f:
        data = json.load(f)
    mcs_index = [entry["mcs_index"] for entry in data]
    bler = [entry["bler"] for entry in data]
    num_rbs = [entry["num_rbs"] for entry in data]
    return mcs_index, bler, num_rbs

def plot_charts(all_data):
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle("MCS Performance Metrics", fontsize=14, fontweight="bold")

    n_files = len(all_data)
    # Use the union of all MCS indices across files, sorted
    all_mcs = sorted(set(idx for _, (mcs, _, _) in all_data for idx in mcs))
    x = np.arange(len(all_mcs))

    # Bar width shrinks as more files are added, with a small gap between groups
    bar_width = min(0.8 / n_files, 0.25)
    colors = plt.cm.tab10.colors  # Up to 10 distinct colors

    for i, (label, (mcs_index, bler, num_rbs)) in enumerate(all_data):
        # Map this file's values to the shared MCS axis (None → 0 if missing)
        bler_mapped   = [bler[mcs_index.index(m)]    if m in mcs_index else 0 for m in all_mcs]
        num_rbs_mapped = [num_rbs[mcs_index.index(m)] if m in mcs_index else 0 for m in all_mcs]

        offset = (i - (n_files - 1) / 2) * bar_width
        color  = colors[i % len(colors)]

        axes[0].bar(x + offset, bler_mapped,    width=bar_width, label=label, color=color, alpha=0.85)
        axes[1].bar(x + offset, num_rbs_mapped, width=bar_width, label=label, color=color, alpha=0.85)

    for ax, ylabel, title, ylim in [
        (axes[0], "BLER (Block Error Rate)",       "MCS Index vs BLER",              (0, 1.05)),
        (axes[1], "Number of Resource Blocks (RBs)", "MCS Index vs Number of RBs",   (0, None)),
    ]:
        ax.set_title(title)
        ax.set_xlabel("MCS Index")
        ax.set_ylabel(ylabel)
        ax.set_xticks(x)
        ax.set_xticklabels(all_mcs)
        if ylim[1] is not None:
            ax.set_ylim(*ylim)
        ax.grid(True, linestyle="--", alpha=0.6, axis="y")
        ax.legend(fontsize=8)

    plt.tight_layout()
    plt.savefig("img/mcs_charts.png", dpi=150)
    print("Charts saved to mcs_charts.png")
    plt.show()

def main():
    if len(sys.argv) < 2:
        print("Usage: python plot_mcs.py <file1.json> [file2.json ...]")
        sys.exit(1)

    all_data = []
    for filepath in sys.argv[1:]:
        label = os.path.splitext(os.path.basename(filepath))[0]  # filename without extension
        try:
            all_data.append((label, load_data(filepath)))
        except FileNotFoundError:
            print(f"Error: File '{filepath}' not found.")
            sys.exit(1)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error parsing '{filepath}': {e}")
            sys.exit(1)

    plot_charts(all_data)

if __name__ == "__main__":
    main()