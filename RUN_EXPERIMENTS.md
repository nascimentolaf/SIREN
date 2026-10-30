# Reproducing the paper's results

This guide reruns, on one machine, the experiments behind the Performance
Evaluation section of *SIREN: Table-Driven Real-Time MAC Scheduler*, and
regenerates its table, figures and macros. One script does it all,
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
Python 3 with `numpy`, `pandas` and `matplotlib` (details:
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

Its four stages, in order:

| Stage | What it does | Writes |
| :--- | :--- | :--- |
| `build` | `make` of the planner, the baselines and `siren` (step 2) | the binaries |
| `sweep` | every pair (`B^max`, flowset) of the 1000 flowsets of `output/flowsets/` at 51, 79, 107 and 135 RBs: the planner and `siren`, then MUST, QoS-PF, RR and PF | `output/sweep/rb<B>/` |
| `rq4` | RQ4, once the sweep is complete: `analysis/rq4_hyperperiod.py prepare` draws 100 flowsets that SIREN schedules at 135 RBs, takes their minimum-peak tuple and stretches their periods to hyperperiods of 420, 840, 2520, 8400 and 25 200 ms; `run --solver offline_phase/siren/siren` solves each model at 135 RBs | `output/rq4_siren/` |
| `analysis` | from empty CSVs: `scripts/sweep_experiment.sh --collect`, `rq4_hyperperiod.py collect`, `plot_paper.py --derive`, `plot_paper.py` | `analysis/csv/`, `analysis/figures/paper/` |

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
| RQ4 | 100 flowsets, at 135 RBs, 1 h (3600 s) per model; the models above 8400 ms are solved 12 at a time, since each needs several GB |

The rest comes from `scripts/parameters.sh` (TTI 0.25 ms, upper-layer delay
4 ms, `A^max = 4`, the QoS-PF and PF parameters, …). The script's header lists
the variables that override its settings: they are meant for tests only, and a
run that uses them does not reproduce the paper.

**Cost.** This guide does not estimate how long the run takes on your
machine. What bounds it, per budget and per flowset, is the number of tuples
SIREN decides, each within 60 s of wall-clock time, followed by the peak search
over the schedulable tuples; `check_catalogue.py` prints the tuples per
flowset (a median of 1200 and at most 11 000 for the shipped catalogue). RQ4
adds at most 500 models (100 flowsets, 5 hyperperiods), of at most 1 h each. The baselines take a negligible time next
to the ILP.

## 5. Resume, stages, flowsets, shards

**Resume.** A pair is done when its status file,
`output/sweep/rb<B>/.parallel/status/<flowset>`, says `ok`. After an
interruption, relaunch the same command: it runs only the pairs not done, and
RQ4 skips the models that have a result. A pair that failed says
`fail <methods>` and is run again at the next launch. The sweep stage stops
with an error if some pair is not `ok` at its end.

**Some stages only.** `--stages` takes a list of `build`, `sweep`, `rq4` and
`analysis`:

```bash
./scripts/run_paper_experiment.sh --stages "build"
./scripts/run_paper_experiment.sh -j <workers> --stages "sweep rq4"
./scripts/run_paper_experiment.sh --stages "analysis"
```

The `rq4` and `analysis` stages wait until every pair of the sweep is `ok`,
and `analysis` until every RQ4 model has a result.

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
shard's sweep is done, and runs the analysis once every shard's RQ4 models are
done; every shard solves its share of the pairs and of the RQ4 models. The
`-j` of all machines together must stay within the license.

## 6. The outputs

| Where | What |
| :--- | :--- |
| `output/sweep/rb<B>/reports/<method>/<flowset>/` | the reports and dispatch tables of every pair (`doc/output_formats.md`) |
| `output/sweep/rb<B>/logs/<method>/<flowset>.log` | what each method printed; the ILP's lists every tuple with its time |
| `output/sweep/rb<B>/.parallel/status/<flowset>` | `ok`, or `fail <methods>` |
| `output/rq4_siren/flowset_*/H*.json`, `*.result.json` | the RQ4 models and their results |
| `output/paper_run/` | per machine: `manifest_<host>.txt` (start time, CPU model and cores, shard and workers, stages, flowsets, settings, git commit) and `pairs_<host>.tsv` (one line per pair: budget, flowset, start and end as Unix times, status); the logs of the build, RQ4 and analysis stages |
| `analysis/csv/` | `output_rb<B>.csv` (the verdict table of each budget), `output_sweep.csv` (all budgets), `derived_baseline_configs.csv`, `derived_ilp_timing_rb135.csv`, `rq4_hyperperiod.csv` |
| `analysis/figures/paper/` | the paper's table (`table_rq2.tex`), figures (`fig_*.pdf` and `.png`) and macros (`numbers_results.tex`) |

`doc/analysis/index.md` describes each CSV, figure and macro.

**Comparing with the paper's results.** `results/` will hold the outputs of
the paper's run, with a README that says what each file holds. Compare
`analysis/csv/` and `analysis/figures/paper/` with them. What to expect:

- the baselines are deterministic: their verdicts and peaks must match the
  paper's exactly;
- SIREN's results can differ where a tuple is near the 60 s cap, which is
  wall-clock time: such a tuple may be decided on one machine and not on
  another, which can change a verdict (undecided against schedulable or
  unschedulable) or a peak;
- every time (per tuple, first table, RQ4) is a measurement on your machine.
