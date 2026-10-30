import json
import sys
import matplotlib.pyplot as plt


def load_data(filepath):
    with open(filepath, "r") as f:
        data = json.load(f)

    mcs_index = [entry["mcs_index"] for entry in data]
    bler = [entry["bler"] for entry in data]
    num_rbs = [entry["num_rbs"] for entry in data]

    return mcs_index, bler, num_rbs


def plot_charts(mcs_index, bler, num_rbs, filepath):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("MCS Performance Metrics", fontsize=14, fontweight="bold")

    # MCS Index vs BLER
    axes[0].plot(mcs_index, bler, marker="o", color="steelblue", linewidth=2, markersize=5)
    axes[0].set_title("MCS Index vs BLER")
    axes[0].set_xlabel("MCS Index")
    axes[0].set_ylabel("BLER (Block Error Rate)")
    axes[0].set_ylim(0, 1.05)
    axes[0].set_xticks(mcs_index)
    axes[0].grid(True, linestyle="--", alpha=0.6)

    # MCS Index vs Number of RBs
    axes[1].plot(mcs_index, num_rbs, marker="s", color="darkorange", linewidth=2, markersize=5)
    axes[1].set_title("MCS Index vs Number of RBs")
    axes[1].set_xlabel("MCS Index")
    axes[1].set_ylabel("Number of Resource Blocks (RBs)")
    axes[1].set_xticks(mcs_index)
    axes[1].grid(True, linestyle="--", alpha=0.6)

    plt.tight_layout()
    fig_name = f"{filepath.replace('data/', 'img/').replace('json', 'png')}"
    plt.savefig(fig_name, dpi=150)
    print(f"Charts saved to {fig_name}")
    plt.show()


def main():
    if len(sys.argv) != 2:
        print("Usage: python plot_mcs.py <path_to_json_file>")
        sys.exit(1)

    filepath = sys.argv[1]

    try:
        mcs_index, bler, num_rbs = load_data(filepath)
    except FileNotFoundError:
        print(f"Error: File '{filepath}' not found.")
        sys.exit(1)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing JSON file: {e}")
        sys.exit(1)

    plot_charts(mcs_index, bler, num_rbs, filepath)


if __name__ == "__main__":
    main()
