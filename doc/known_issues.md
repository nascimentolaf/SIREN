# Known issues

What is inconsistent or easy to trip over in the code as shipped. The
documentation describes the code **as it behaves**; this page lists where that
differs from what one would expect.

## Limitations

| Issue | Where | Effect |
| :--- | :--- | :--- |
| Every `siren` process holds one Gurobi license token for its whole run | [`run_paper_experiment.sh`](../scripts/run_paper_experiment.sh), `siren` | with a license limited to `W` processes at once, the workers of all machines together must stay within `W`; a `siren` started beyond it exits with status 1 and its pair is marked `fail` (rerun to retry) |
| The analysis stage needs the default `OUTPUT_DIR` | [`run_paper_experiment.sh`](../scripts/run_paper_experiment.sh) | `plot_paper.py` reads `output/sweep/`; a run in another output tree can run every stage but `analysis` |
| `sweep_experiment.sh --collect` writes into the repository's `analysis/csv/` | [`sweep_experiment.sh`](../scripts/sweep_experiment.sh) | whatever `OUTPUT_DIR` says; collect another tree with `analysis/collect_verdicts.py` directly |
| The flowset shuffle uses `std::shuffle`, whose algorithm is implementation-defined | `generate_flowset/src/main.cpp` | the same `FLOWSET_SEED` can draw a different batch with another C++ standard library |

## Code that names programs not in this repository

The code was written alongside other tools, which this repository does not
include. The code that refers to them was left unchanged:

- **`analysis/rq4_hyperperiod.py run --solver`** defaults to a solver binary
  that is not shipped: always pass `--solver offline_phase/siren/siren`,
  as `run_paper_experiment.sh` does.
- **`scripts/sweep_experiment.sh`** without `--collect` runs a sweep through a
  script that is not shipped, and fails; only `--collect` is used.
- **`scripts/parameters.sh`** defines settings that no shipped script reads
  (`METHOD_VARIANTS`, whose default names a baseline that is not shipped;
  `REUSE_FLOWSETS`, `CLEAN_OUTPUT`; the paths of other tools); see
  [Configuration](configuration.md#inputs-and-outputs).
- **`analysis/collect_verdicts.py --per-config`** reads the CSV of a
  per-configuration sweep that is not shipped.
- **The analysis code knows the methods `edf`, `5gtps` and `5gtps_harq`**
  (labels, colours, macros). Runs of this repository have no such rows.

## Behaviours to keep in mind when reading results

These are deliberate, but change how a verdict reads.

- **Baselines can exceed `A^max`.** They select from the unscreened
  configuration set, within `A^max` whenever a configuration within it fits
  the cell; where none does they fall back to more attempts, and
  `plot_paper.py` reclassifies such runs as `ko_amax` (the raw CSVs do not).
  On the shipped catalogue and MCS table no application needs the fallback,
  at any budget. See [Baselines](baselines/index.md#configuration-selection).
- **The ILP's per-candidate cap is wall-clock time.** A candidate near the
  60 s cap may be decided on one machine and not on another, or under another
  load. `run_paper_experiment.sh` pins each worker to its own physical core
  for this reason. Every ILP log names its machine (`Host:`), and
  `plot_paper.py --derive` carries it into the timing table (`host`). The
  baselines are deterministic and do not depend on the machine.
- **ILP counts depend on the budgets**: how many tables, how many candidates
  tested. See [Analysis](analysis/index.md#things-to-know-before-trusting-a-number).
