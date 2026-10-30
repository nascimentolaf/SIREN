# SIREN documentation

This repository holds the code and data of the paper *SIREN: Table-Driven
Real-Time MAC Scheduler*. It studies **table-driven real-time MAC scheduling
for URLLC traffic in a private 5G gNB**. Each periodic downlink flow is turned
into real-time tasks, one per transport block (TB), and scheduled over a
three-dimensional resource grid: **resource blocks × HARQ processes × time**.
An offline time-indexed ILP, solved with Gurobi, computes a cyclic schedule
table that provably meets every deadline over one hyperperiod. Four baseline
schedulers (MUST, QoS-PF, RR and PF) run on the same inputs, and analysis
scripts compare them.

The paper calls the proposed scheduler **SIREN** (Scheduling with ILP for
Real-time, Error-bounded New Radio). In the code, the reports and the CSVs it
is the method `ilp`, solved by the program `siren`
(`offline_phase/siren/`).

The code is organised as a pipeline:

```text
 link simulation (bler/)          ->  input/mcs_configurations.json  (BLER and RBs per TBS x MCS)
 application catalogue (input/)   ->  flowsets (offline_phase/generate_flowset)
 flowset + MCS table              ->  configurations per flow (offline_phase/planning, Section V)
 configurations                   ->  ILP schedule tables (offline_phase/siren)
 flowset + MCS table              ->  baseline schedule (baseline_algorithms/<method>)
 all reports                      ->  verdict tables, the paper's table, figures and macros (analysis/)
```

`scripts/run_paper_experiment.sh` runs the paper's experiments, from the
committed flowsets to the table, figures and macros.

## Where to start

| If you want to… | Read |
| :--- | :--- |
| install the requirements and build | [Getting started](getting_started.md) |
| reproduce the paper's results | the run guide, `RUN_EXPERIMENTS.md` at the repository root |
| understand how the pieces fit and what they promise | [Architecture](architecture.md) |
| change a parameter (RBs, HARQs, timeouts…) | [Configuration](configuration.md) |
| change or understand the applications and the MCS table | [Inputs](input.md) |
| run the experiments: stages, workers, resume, shards | [Scripts](scripts.md) |
| build and run `siren`, and its Gurobi license | [ILP evaluation](offline_phase/evaluation.md) |
| know what a report file contains | [Output formats](output_formats.md) |
| turn reports into the paper's table, figures and macros | [Analysis](analysis/index.md) |
| regenerate the MCS table from link-level simulation | [BLER and MCS tables](bler.md) |
| know what is broken or easy to trip over | [Known issues](known_issues.md) |
| know who is behind the project and who funds it | [About us](about.md) |

## Components

| Component | Directory | Page |
| :--- | :--- | :--- |
| Flowset generator | `offline_phase/generate_flowset/` | [Flowset generation](offline_phase/generate_flowset.md) |
| Configuration planner (Section V) | `offline_phase/planning/`, `common/` | [Configuration planning](offline_phase/planning.md) |
| ILP scheduler, `siren` (Section VI) | `offline_phase/siren/` | [ILP evaluation](offline_phase/evaluation.md) |
| Baseline schedulers | `baseline_algorithms/` | [Baselines](baselines/index.md) |
| Verdicts, figures, RQ4 | `analysis/` | [Analysis](analysis/index.md) |
| Link-level BLER simulation | `bler/` | [BLER and MCS tables](bler.md) |

## Conventions used in these pages

- **Section and equation numbers** (Section V, Eq. (4), …) refer to the
  paper, which the code comments cite the same way.
- **Equations** are written in plain text: `E^tb = 1 - (1 - E_i)^(1/N)` means
  E superscript tb, `E_i` means E subscript i.
- **Paths** are relative to the repository root unless stated otherwise.

## Building these pages

The Markdown in `doc/` is the source; `doc/html/` is generated from it:

```bash
python3 doc/build_docs.py          # rebuild doc/html/
python3 doc/build_docs.py --check  # check every link, write nothing
```

The script needs the `markdown` Python package (`pip install markdown`). The
sidebar order is `doc/nav.txt`. The script's own header documents what Markdown
it accepts and how links are rewritten. Open `doc/html/index.html` in a
browser: the pages work offline, straight from disk.
