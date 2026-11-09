# Reproducing the paper's results

This guide reruns, on one machine, the experiments behind the Performance
Evaluation section of *SIREN: Table-Driven Real-Time MAC Scheduler*, and
regenerates its tables, figures and macros. One script does it all,
`scripts/run_paper_experiment.sh`. SIREN is the method `ilp` in every
directory and CSV below, solved by the program `siren` with Gurobi.

Every command starts from the repository root unless it says otherwise. The
documentation in `doc/` (open `doc/html/index.html`) describes every script
and option used here; `./scripts/run_paper_experiment.sh --help` prints the
script's usage.

| Step | What |
| :--- | :--- |
| 1 | [install Gurobi and its license, set `GUROBI_HOME`](#1-gurobi-and-its-license) |
| 2 | [build](#2-build) |
| 3 | [check the catalogue](#3-check-the-catalogue) |
| 4 | [run everything](#4-run-everything) |
| 5 | [resume, run some stages, some flowsets, or several machines](#5-resume-stages-flowsets-shards) |
| 6 | [the outputs, and how to compare them with the paper's](#6-the-outputs) |

## 1. Gurobi and its license

SIREN's ILP is solved by Gurobi 13.0 through its C API; the paper's runs used
Gurobi 13.0.1. Neither the repository nor its scripts install it:

1. Download the Gurobi 13.0 distribution for Linux from Gurobi
   (`gurobi13.0.1_linux64.tar.gz` for 13.0.1) and unpack it. The `gurobipy`
   wheel from pip is not enough: `siren` needs the C headers and library.
2. Point `GUROBI_HOME` at its `linux64` directory, which holds
   `include/gurobi_c.h` and `lib/libgurobi130.so`:

   ```bash
   export GUROBI_HOME=/path/to/gurobi1301/linux64
   ```

3. Get a Gurobi license of your own and install its file as Gurobi
   documents. `siren` finds it through `GRB_LICENSE_FILE`, or in Gurobi's
   default places such as `~/gurobi.lic`. Keep the file out of the repository
   (`.gitignore` ignores every `gurobi.lic`).

Each `siren` process takes one license token for its whole run, so the
number of workers (step 4) is limited by the number of processes your license
allows at once. A `siren` that gets no license exits with status 1 before
solving anything, and its pair is marked `fail`.

## 2. Build

The other requirements: CMake ≥ 3.13, `make`, `g++` with C++20 support,
`nlohmann/json`, `bc`, `lscpu`, `taskset` and `flock` (util-linux), and
Python 3 with `numpy`, `pandas`, `matplotlib` and `scipy` (details:
`doc/getting_started.md`).

```bash
(cd scripts && ./install.sh)      # or ./install_conda.sh inside an activated conda environment
make -C common test               # expects: All Section V checks passed.
```

| Builder | Builds |
| :--- | :--- |
| `scripts/install.sh`, `scripts/install_conda.sh` | the flowset generator, the planner, `siren` and the four baselines; installs nlohmann/json first if CMake cannot find it (`apt-get`, or `conda-forge::nlohmann_json`) |
| the `build` stage of `run_paper_experiment.sh` (step 4) | the planner, the baselines and `siren` (the experiments do not run the flowset generator) |
| `make -C common test` | the unit test of the shared configuration code, and runs it |

Building needs `GUROBI_HOME` but no license. CMake prints
`Found Gurobi: .../libgurobi130.so`, or stops with
`Gurobi 13.0 not found under GUROBI_HOME=...`.

## 3. Check the catalogue

```bash
python3 scripts/check_catalogue.py
```

It must end with `all invariants hold`. `--table` prints the configurations
per reliability class.

## 4. Run everything

```bash
./scripts/run_paper_experiment.sh -j <workers>
```

`-j` is the number of workers. The default is one per physical core (as
`lscpu` reports them), and more is refused. Each worker is pinned to its own
physical core and runs one pair (budget, flowset) at a time, `siren` and every
baseline of it in that core, so no two solvers share a core. Each worker holds
at most one `siren`, and so one license token, at a time: keep `-j` within
your license. A long run is best detached:

```bash
nohup ./scripts/run_paper_experiment.sh -j <workers> >run.log 2>&1 &
```

Its five stages, in order:

| Stage | What it does | Writes |
| :--- | :--- | :--- |
| `build` | `make` of the planner, the baselines and `siren` (step 2) | the binaries |
| `sweep` | every pair (`B^max`, flowset) of the 1000 flowsets of `output/flowsets/` at 51, 79, 107 and 135 RBs: the planner and `siren`, then MUST, QoS-PF, RR and PF | `output/sweep/rb<B>/` |
| `rq4` | RQ4, once the sweep is complete: `analysis/rq4_hyperperiod.py prepare --sample 1000` takes every flowset SIREN schedules at 135 RBs, with its minimum-peak tuple, and stretches its periods to hyperperiods of 420, 840, 2520, 8400 and 25 200 ms; `run --solver offline_phase/siren/siren -j 12` solves each model at 135 RBs, 12 at a time | `output/rq4_siren/` |
| `search` | once the sweep is complete: `analysis/sweep_configurations.py`, once per budget on that budget's reports, runs every baseline of `METHODS` on every tuple SIREN decided, with that tuple imposed (`FLOW_CONFIG`), `-j` processes at a time. This is the paper's "baseline + search", an ablation of SIREN: it needs SIREN's configuration analysis and an offline simulation of every tuple, so it is not a scheduler a gNB runs | `output/per_config/rb<B>.csv`, one row per tuple and method (about 9 million rows and 700 MB per budget), and `rb<B>.log` |
| `analysis` | from empty CSVs: `scripts/sweep_experiment.sh --collect`, `rq4_hyperperiod.py collect`, `plot_paper.py --derive` (which also reduces `output/per_config/` to `baseline_search.csv`), `plot_paper.py` | `analysis/csv/`, `analysis/figures/paper/` |

**Settings.** The script sets the paper's settings itself:

| Setting | Value |
| :--- | :--- |
| RB budgets `B^max` | 51, 79, 107 and 135 RBs |
| HARQ processes `M` | 3 |
| methods | `ilp` (SIREN, solved by `siren`), `must`, `qospf`, `rr`, `pf` |
| ILP objective | `rb_peak`, with the bounded search: decide every tuple, then minimise the peak RB usage over the schedulable ones |
| time per ILP tuple | 60 s (wall-clock) |
| time per flowset | no limit |
| Gurobi threads | 1 (set in `siren`) |
| optimality gap | 0 (`MIPGap` and `MIPGapAbs`, set in `siren`) |
| RQ4 | `RQ4_SAMPLE` 1000, which takes every flowset SIREN schedules at 135 RBs; at 135 RBs, under the feasibility objective, 1 h (3600 s) per model (`RQ4_CAP`); `RQ4_JOBS` 12 models at a time at every `H*`, since the largest models need several GB each and every target runs at the same concurrency, so that their times compare |
| search | every baseline of `METHODS` (`must`, `qospf`, `rr`, `pf`) on every tuple, at every budget, `-j` processes at a time |

The rest comes from `scripts/parameters.sh` (TTI 0.25 ms, upper-layer delay
4 ms, `A^max = 4`, the QoS-PF and PF parameters, …). The script's header lists
the variables that override its settings: they are meant for tests only, and a
run that uses them does not reproduce the paper. The paper's run used `-j 50`
on each of two servers (`--shard`, step 5); `results/README.md` describes it.

**Cost.** This guide does not estimate how long the run takes on your
machine. What bounds it, per budget and per flowset, is the number of tuples
SIREN decides, each within 60 s of wall-clock time, followed by the peak search
over the schedulable tuples. The 1000 flowsets have a median of 1260 tuples and
at most 11 000 (`MedianTuples` and `MaxTuples` in
`results/figures/numbers_results.tex`). RQ4 adds one model per flowset SIREN
schedules at 135 RBs and target hyperperiod (4816 in the paper's run), of at
most 1 h each, 12 at a time. The search runs each of the four baselines once
per tuple: 1 821 541 tuples per budget in the paper's run. The baselines take
a negligible time next to the ILP.

## 5. Resume, stages, flowsets, shards

**Resume.** A pair is done when its status file,
`output/sweep/rb<B>/.parallel/status/<flowset>`, says `ok`. After an
interruption, relaunch the same command: it runs only the pairs not done, and
RQ4 skips the models that have a result. A pair that failed says
`fail <methods>` and is run again at the next launch. The sweep stage stops
with an error if some pair is not `ok` at its end.

**Some stages only.** `--stages` takes a list of `build`, `sweep`, `rq4`,
`search` and `analysis`:

```bash
./scripts/run_paper_experiment.sh --stages "build"
./scripts/run_paper_experiment.sh -j <workers> --stages "sweep rq4 search"
./scripts/run_paper_experiment.sh --stages "analysis"
```

The `rq4`, `search` and `analysis` stages wait until every pair of the sweep
is `ok`, and `analysis` until every RQ4 model has a result and every budget's
search is done. A budget's search is done when `output/per_config/.rb<B>.done`
exists; its CSV is written under a temporary name and renamed at the end, so a
stopped search leaves no partial CSV, and is run again at the next launch.

**Some flowsets only.** `--flowsets FILE` runs only the flowsets FILE lists,
one name (`flowset_NNNN`) per line:

```bash
printf 'flowset_%04d\n' 1 2 3 > some.txt
./scripts/run_paper_experiment.sh -j <workers> --stages "sweep" --flowsets some.txt
```

The paper's numbers are on all 1000 flowsets: a run on a subset gives other
numbers.

**Another output tree.** `OUTPUT_DIR=<dir>` writes the run under `<dir>`
instead of `output/`; `<dir>` needs a `flowsets/` folder, for example a link
to `output/flowsets`. Every stage but `analysis` works there: `plot_paper.py`
reads `output/sweep/`.

**Several machines on one shared disk.** With `--shard K/N`, the K-th of N
machines that see the same repository tree takes the flowsets whose position
in the list is K modulo N:

```bash
./scripts/run_paper_experiment.sh -j <workers> --shard 1/2    # on machine 1
./scripts/run_paper_experiment.sh -j <workers> --shard 2/2    # on machine 2
```

Shard 1 builds (the others wait for it), runs RQ4's `prepare` once every
shard's sweep is done, and runs the analysis once every shard's RQ4 models and
search are done; every shard solves its share of the pairs and of the RQ4
models, and runs the search on the budgets whose position in `RBS` is K modulo
N. The `-j` of all machines together must stay within the license.

## 6. The outputs

| Where | What |
| :--- | :--- |
| `output/sweep/rb<B>/reports/<method>/<flowset>/` | the reports and dispatch tables of every pair (`doc/output_formats.md`) |
| `output/sweep/rb<B>/logs/<method>/<flowset>.log` | what each method printed; the ILP's lists every tuple with its time |
| `output/sweep/rb<B>/.parallel/status/<flowset>` | `ok`, or `fail <methods>` |
| `output/rq4_siren/flowset_*/H*.json`, `*.result.json` | the RQ4 models and their results |
| `output/per_config/rb<B>.csv`, `rb<B>.log` | the search: one row per tuple and method (the `ilp` row repeats SIREN's verdict for the tuple), about 9 million rows and 700 MB per budget; and what `sweep_configurations.py` printed |
| `output/paper_run/` | per machine: `manifest_<host>.txt` (start time, CPU model and cores, shard and workers, stages, flowsets, settings, git commit) and `pairs_<host>.tsv` (one line per pair: budget, flowset, start and end as Unix times, status); the logs of the build, RQ4 and analysis stages |
| `analysis/csv/` | `output_rb<B>.csv` (the verdict table of each budget), `output_sweep.csv` (all budgets), `derived_baseline_configs.csv`, `derived_ilp_timing_rb135.csv`, `rq4_hyperperiod.csv`, `rq4_statistics.csv`, `rq4_regression.json`, `baseline_search.csv` |
| `analysis/figures/paper/` | the paper's tables (`table_rq2.tex`, Table IV, and `table_rq4.tex`, Table V), figures (`fig_*.pdf` and `.png`) and macros (`numbers_results.tex`) |

`doc/analysis/index.md` describes each CSV, figure and macro.

**Comparing with the paper's results.** `results/` holds the outputs of the
paper's run, and [`results/README.md`](results/README.md) says what each file
holds. A new run writes into `output/` and `analysis/`, which git ignores, and
leaves `results/` as it is. After a run of all the stages:

```bash
# the macros and the tables
diff analysis/figures/paper/numbers_results.tex results/figures/numbers_results.tex
diff analysis/figures/paper/table_rq2.tex results/figures/table_rq2.tex
diff analysis/figures/paper/table_rq4.tex results/figures/table_rq4.tex
# the search
diff analysis/csv/baseline_search.csv results/csv/baseline_search.csv
# the verdict tables, baselines only: every column of every row
for b in 51 79 107 135; do
    diff <(grep -v ',ilp,' analysis/csv/output_rb$b.csv) <(grep -v ',ilp,' results/csv/output_rb$b.csv)
done
```

What to expect:

- the baselines are deterministic: their rows must match the paper's exactly;
- SIREN's results can differ where a tuple is near the 60 s cap, which is
  wall-clock time: such a tuple may be decided on one machine and not on
  another, which can change a verdict (undecided against schedulable or
  unschedulable) or a peak. Its rows hold solving times too
  (`solve_time_ms_median`, `solve_time_ms_max`), so compare their `verdict` and
  `rb_peak`, not the whole row;
- every time (per tuple, first table, RQ4) is a measurement on your machine, so
  the macros that hold times differ, and with them `numbers_results.tex`,
  `table_rq4.tex`, `rq4_statistics.csv` and `rq4_regression.json`;
- the search runs the deterministic baselines on every tuple SIREN decided,
  which is every tuple of every flowset since SIREN has no budget per flowset,
  so `baseline_search.csv` must match the paper's; the `Search*` macros also
  read SIREN's verdicts and peaks, so they follow the remark above;
- `host` holds your machine's name, where `results/` has `server1` or `server2`;
- RQ4's `prepare` takes every flowset SIREN schedules at 135 RBs and its
  minimum-peak tuple, so the `H*.json` of `output/rq4_siren/` match
  `results/rq4_models/` when the sweep's reports agree on these, with Python
  3.10 or 3.11; with Python 3.12 or later, 71 of them get other periods, equally
  valid (see [Known issues](doc/known_issues.md#limitations)).

To check the paper's numbers without running anything, regenerate them from
`results/csv/` as `results/README.md` says.
