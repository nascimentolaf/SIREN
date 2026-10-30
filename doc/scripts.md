# Scripts

`scripts/` builds the code and runs the paper's experiments. Settings come
from the environment (see [Configuration](configuration.md)); every script
resolves its paths from its own location, so it can be launched from anywhere.

| Script | Purpose |
| :--- | :--- |
| `install.sh` | build every C++ component, `siren` included (installs nlohmann/json if missing) |
| `install_conda.sh` | the same inside an activated conda environment |
| `parameters.sh` | defaults for every setting; sourced by the others, never executed |
| `run_paper_experiment.sh` | the paper's experiments: build, RB-budget sweep, RQ4, then the table, figures and macros |
| `sweep_experiment.sh` | its `--collect` mode reduces the sweep's reports to CSVs; `run_paper_experiment.sh` calls it |
| `check_catalogue.py` | validate the application catalogue (see [Inputs](input.md#checking-the-catalogue)) |

---

## `install.sh` and `install_conda.sh`

```bash
export GUROBI_HOME=/path/to/gurobi1301/linux64
./install.sh            # or ./install_conda.sh inside an activated conda environment
```

1. If CMake cannot find the `nlohmann_json` package that `siren`'s build asks
   for (`find_package(nlohmann_json 3.2.0)`, tried in a throwaway project),
   installs `nlohmann-json3-dev` with `sudo apt-get` (`conda-forge::nlohmann_json`
   in the conda variant). Every other component bundles its own `json.hpp`.
2. Runs `make clean && make -j$(nproc)` in: `offline_phase/generate_flowset`,
   `offline_phase/planning`, `offline_phase/siren`, and
   `baseline_algorithms/` (one Makefile for the four baselines, each built to
   `<name>/<name>`).

It stops at the first failure (`set -e`). Neither script installs Gurobi:
`siren`'s build needs `GUROBI_HOME` (see
[ILP evaluation](offline_phase/evaluation.md#build)). It does not touch
`output/`: the `make clean` of each component removes build outputs only. It
does not build the unit test of `common/` (`make -C common test`).

---

## `parameters.sh`

Sourced by the other scripts. Defines the paths (all derived from the
repository root, `OUTPUT_DIR` overridable) and every run parameter with an
overridable default. The full table is in [Configuration](configuration.md).

It makes `APP_DATABASE`, `MCS_CONFIG` and `OUTPUT_DIR` absolute, resolving a
relative value against `$LAUNCH_DIR`, or against the current directory when
`LAUNCH_DIR` is unset. Every binary runs from its own directory, so a relative
path left as it is would point somewhere else for each of them.

---

## `run_paper_experiment.sh`

Everything the paper's Performance Evaluation reports, in one run: SIREN's
ILP solved by `siren` and the four baselines MUST, QoS-PF, RR and PF, on the
1000 committed flowsets of `output/flowsets/`. Its header is its usage
(`./run_paper_experiment.sh --help`).

```bash
./run_paper_experiment.sh                     # every stage, one worker per physical core
./run_paper_experiment.sh -j 8                # 8 workers
./run_paper_experiment.sh --stages "sweep"    # some stages only (build sweep rq4 analysis)
./run_paper_experiment.sh --flowsets FILE     # only the flowsets FILE lists (one name per line)
./run_paper_experiment.sh -j 8 --shard 1/2    # machine 1 of 2 sharing this tree on one disk
./run_paper_experiment.sh -j 8 --shard 2/2    # machine 2
```

| Stage | What it does | Writes |
| :--- | :--- | :--- |
| `build` | `make` in `offline_phase/planning`, `baseline_algorithms` and `offline_phase/siren`; stops if `GUROBI_HOME` is not set | the binaries; `output/paper_run/build_*.log` |
| `sweep` | every (`B^max`, flowset) pair at 51, 79, 107 and 135 RBs: the planner and `siren`, then each baseline | `output/sweep/rb<B>/reports/<method>/<flowset>/`, `logs/<method>/<flowset>.log`, `.parallel/status/<flowset>` |
| `rq4` | `analysis/rq4_hyperperiod.py`: `prepare` (100 flowsets that `siren` schedules at 135 RBs, their minimum-peak tuple, periods stretched to the target hyperperiods), then `run --solver offline_phase/siren/siren`, 1 h per model | `output/rq4_siren/` |
| `analysis` | from empty CSVs: `sweep_experiment.sh --collect`, `rq4_hyperperiod.py collect`, `plot_paper.py --derive`, `plot_paper.py` | `analysis/csv/`, `analysis/figures/paper/` |

**Settings.** The script sets the paper's settings itself:

| Setting | Value |
| :--- | :--- |
| `RBS` | `51 79 107 135` |
| `HARQs` (`M`) | `3` |
| `METHODS` | `ilp must qospf rr pf` (`ilp`: SIREN, solved by `siren`) |
| `ILP_OBJECTIVE`, `ILP_RB_PEAK_SEARCH` | `rb_peak`, `bounded` (not overridable) |
| `ILP_TIMEOUT` | `0`: no budget per flowset (not overridable) |
| `ILP_CANDIDATE_TIMEOUT` | `60` s per ILP tuple (wall-clock) |
| Gurobi threads | one, set in `siren` |
| `RQ4_SAMPLE`, `RQ4_CAP` | `100` flowsets, `3600` s per model, at 135 RBs (`RQ4_RB`) |
| `RQ4_SPLIT_H`, `RQ4_JOBS_LARGE` | RQ4 solves the models up to 8400 ms with `-j` workers, then the larger ones `12` at a time (they need several GB each) |

`RBS`, `HARQs`, `METHODS`, `ILP_CANDIDATE_TIMEOUT` and the `RQ4_*` values can
be overridden in the environment, for tests only. The rest comes from
`parameters.sh` (TTI, `t_up`, `A^max`, the QoS-PF and PF parameters, …).

**Workers.** `-j` is the number of workers (default: one per physical core,
as `lscpu` reports them; no more is accepted). Each worker is pinned
(`taskset`) to its own physical core, on its first hardware thread, and runs
one pair at a time, the ILP and every baseline of it, so no two solvers share
a core and the 60 s cap means the same everywhere. Each `siren` process takes
one Gurobi license token: with a license limited to `W` processes at once,
the `-j` of all machines together must stay within `W`.

**Order.** Pairs run flowset by flowset in the order of their numbers, every
budget of a flowset before the next flowset, so that at any time the flowsets
finished at every budget are, up to the pairs in flight, the lowest-numbered
ones.

**Resume.** A pair is done when `output/sweep/rb<B>/.parallel/status/<flowset>`
says `ok`; relaunching with the same arguments runs only the rest, and RQ4
skips the models that have a result. A pair that failed says `fail <methods>`
and is run again at the next launch. The sweep stage stops with an error if
some pair is not `ok` at its end.

**Shards.** With `--shard K/N`, the K-th machine takes the flowsets whose
position in the list is K modulo N. Every machine must see the same tree (a
shared disk). Shard 1 builds (the others wait for it), runs RQ4's `prepare`
once every shard's sweep is done, and, once every shard's RQ4 share is done,
the analysis; every shard solves its share of RQ4's models.

**Checks.** Every stage refuses a tree whose ILP reports do not come from
`siren` (`config.solver` not `gurobi ...`) or whose RQ4 results were not solved
by `siren`: use another `OUTPUT_DIR` for runs of another solver. The analysis
stage needs the default `OUTPUT_DIR` (`output/`), since `plot_paper.py` reads
`output/sweep/`.

**Record.** `output/paper_run/` holds, per machine, `manifest_<host>.txt`
(start time, CPU model and core count, shard and workers, stages, flowsets,
settings, the git commit) and `pairs_<host>.tsv` (one line per pair: budget,
flowset, start and end as Unix times, status), plus the logs of the build,
RQ4 and analysis stages.

A long run is best detached:
`nohup ./run_paper_experiment.sh ... >run.log 2>&1 &`.

---

## `sweep_experiment.sh --collect`

```bash
./sweep_experiment.sh --collect          # every budget of RBS (default 51 79 107 135)
./sweep_experiment.sh --collect --merge  # update the CSVs instead of rewriting them
```

Runs `analysis/collect_verdicts.py` on every budget that has reports under
`$OUTPUT_DIR/sweep/rb<B>/reports`, writing `analysis/csv/output_rb<B>.csv` in
the repository, and stacks them (with a leading `rb` column) into
`analysis/csv/output_sweep.csv`, the input of `analysis/plot_paper.py`. It
always writes into the repository's `analysis/csv/`, whatever `OUTPUT_DIR`
says; to collect another output tree elsewhere, run
`analysis/collect_verdicts.py` on it directly
([Analysis](analysis/index.md#collect_verdictspy)).

With `--merge`, the rows of the methods found in the reports are replaced and
every other row is kept, and a budget without reports keeps its CSV.

---

## `check_catalogue.py`

See [Inputs](input.md#checking-the-catalogue).

---

## Output tree

```text
output/
├── flowsets/flowset_NNNN.json                  # the inputs (tracked in git)
├── sweep/rb<B>/
│   ├── reports/<method>/flowset_NNNN/
│   │   ├── combo_*.json                        # dispatch tables
│   │   ├── planner_report.json                 # ilp only
│   │   └── offline_phase_report.json           # ilp only
│   ├── logs/<method>/flowset_NNNN.log
│   └── .parallel/status/flowset_NNNN           # ok, or fail <methods>
├── rq4_siren/flowset_NNNN/H<H*>.json            # RQ4 models, and their *.result.json
└── paper_run/                                   # manifests, pairs_<host>.tsv, stage logs
```

Everything except `flowsets/` is ignored by git. File formats:
[Output formats](output_formats.md).
