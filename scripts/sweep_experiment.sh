#!/bin/bash
#
# The paper's experiment: the ILP with the rb_peak objective (Section VI-D,
# Eq. (9)) and every baseline, over the flowsets in output/flowsets, once per
# RB budget B^max, each budget in its own output tree:
#
#   output/sweep/rb<B>/{flowsets -> ../../flowsets, reports/, logs/}
#
# The B^max=135 tree (100 MHz FR1 carrier at 60 kHz) answers RQ2 and RQ4 --
# schedulability, the baseline comparison and the solving cost are read from
# it exactly as from a plain output/ run -- and the four trees together answer
# RQ3, the residual capacity against the budget.
#
# Per budget the ILP first decides every candidate tuple of prod_i P_i with
# the feasibility objective (no sweep budget, ILP_TIMEOUT=0; ILP_CANDIDATE_TIMEOUT
# (60) seconds per candidate), then minimises the peak RB usage (Eq. (9)) over
# the schedulable ones in ascending order of their mean RB usage over the
# hyperperiod, stopping as soon as the best peak found is <= ceil(mean) of the
# next candidate -- no schedule's peak is below its mean usage, so the rest
# cannot improve it (ILP_RB_PEAK_SEARCH=bounded). collect_verdicts.py keeps, per
# flowset, the candidate with the smallest peak. A flowset is schedulable if
# any candidate is, unschedulable if every candidate is proved infeasible,
# undecided otherwise (some candidate hit the cap). The residual capacity of a
# table is
#
#   B^max - rb_peak              RBs free in every TTI (guaranteed), and
#   B^max - rb_window_mean_peak  mean RBs free over the worst window of
#                                length t_min, the flowset's shortest period.
#
# Usage (mirrors parallel_experiment.sh; the selector is a flowset range, and
# every budget is run for it, so machines split the flowsets, not the budgets):
#   ./sweep_experiment.sh -j 128 1-267               # every budget, flowsets 1..267
#   RBS="51 135" ./sweep_experiment.sh -j 64 268-534 # two budgets only
#   RESUME=0 ./sweep_experiment.sh -j 128 1-267      # redo flowsets already done
#   ./sweep_experiment.sh --collect                   # reduce reports to CSVs
#
# One method added after the sweep, alone (seconds for 5G-TPS): RESUME=0
# because a flowset's status says ok once any earlier run finished it, and
# --merge so the CSVs keep the other methods' rows, whose reports may be gone.
#   METHODS=5gtps_harq RESUME=0 ./sweep_experiment.sh -j 12
#   ./sweep_experiment.sh --collect --merge
#
# Resume is the default: a flowset whose status file in a budget's
# .parallel/status/ says ok is skipped for that budget, so an interrupted run
# is simply relaunched with the same arguments.
#
# --collect writes analysis/csv/output_rb<B>.csv for every budget with reports
# (analysis/csv/output_rb135.csv is what plot_evaluation.py --verdicts
# consumes for RQ2/RQ4) plus analysis/csv/output_sweep.csv, all budgets
# stacked with an `rb` column, which analysis/plot_paper.py reads.
#
# Fixed settings (override in the environment if needed):
#   RBS="51 79 107 135"  HARQs=3  METHODS="must pf qospf rr ilp"
#   (the 40, 60, 80 and 100 MHz FR1 carriers at 60 kHz SCS, TS 38.101-1)
#   ILP_OBJECTIVE=rb_peak  ILP_RB_PEAK_SEARCH=bounded  ILP_TIMEOUT=0
#   ILP_CANDIDATE_TIMEOUT=60  RESUME=1

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Relative paths in the environment (OUTPUT_DIR, APP_DATABASE, MCS_CONFIG)
# mean relative to where the run was launched; parameters.sh resolves them
# against LAUNCH_DIR, since this script leaves that directory just below.
export LAUNCH_DIR="${LAUNCH_DIR:-$PWD}"
cd "$SCRIPT_DIR"

RBS="${RBS:-51 79 107 135}"
export HARQs="${HARQs:-3}"
export METHODS="${METHODS:-must pf qospf rr ilp}"
export ILP_OBJECTIVE="${ILP_OBJECTIVE:-rb_peak}"
export ILP_RB_PEAK_SEARCH="${ILP_RB_PEAK_SEARCH:-bounded}"
export ILP_TIMEOUT="${ILP_TIMEOUT:-0}"                     # 0 = no sweep budget
export ILP_CANDIDATE_TIMEOUT="${ILP_CANDIDATE_TIMEOUT:-60}" # seconds per candidate
export CLEAN_OUTPUT=0
RESUME="${RESUME:-1}"                                       # 1 = skip flowsets already done

source ./parameters.sh
ROOT_OUTPUT="$OUTPUT_DIR"                 # output/ of the repository
SWEEP_ROOT="$ROOT_OUTPUT/sweep"
SHARED_FLOWSETS="$ROOT_OUTPUT/flowsets"

collect() {   # collect [--merge]
    # --merge updates the CSVs instead of rewriting them: the rows of the
    # methods found in the reports are replaced, every other row is kept --
    # for a method run on its own after the sweep (METHODS=5gtps_harq), whose
    # other methods' reports need not be here. A budget without reports then
    # keeps its CSV, which still goes into output_sweep.csv.
    local merge_flag="${1:-}"
    mkdir -p "$DIR/analysis/csv"
    local all="$DIR/analysis/csv/output_sweep.csv"
    local header_written=0
    for rb in $RBS; do
        local tree="$SWEEP_ROOT/rb$rb"
        local csv="$DIR/analysis/csv/output_rb$rb.csv"
        if [ -d "$tree/reports" ]; then
            python3 "$DIR/analysis/collect_verdicts.py" \
                --reports "$tree/reports" --flowsets "$SHARED_FLOWSETS" \
                --mcs "$MCS_CONFIG" --out "$csv" $merge_flag || return 1
        elif [ -n "$merge_flag" ] && [ -f "$csv" ]; then
            echo "rb=$rb: no reports under $tree, $csv kept as it is"
        else
            echo "rb=$rb: no reports under $tree, skipped"
            continue
        fi
        # Stack the budgets into one table with an rb column.
        if [ $header_written -eq 0 ]; then
            { printf 'rb,'; head -n1 "$csv"; } >"$all"
            header_written=1
        fi
        tail -n +2 "$csv" | sed "s/^/$rb,/" >>"$all"
    done
    [ $header_written -eq 1 ] && echo "Wrote $all"
}

if [ "${1:-}" = "--collect" ]; then
    case "${2:-}" in
        "") collect ;;
        --merge) collect --merge ;;
        *) echo "Unknown option after --collect: $2" >&2; exit 2 ;;
    esac
    exit $?
fi

if [ ! -d "$SHARED_FLOWSETS" ]; then
    echo "ERROR: no flowsets under $SHARED_FLOWSETS" >&2
    exit 1
fi

echo "=============================================="
echo "RB-BUDGET SWEEP (RQ2-RQ4)"
echo "=============================================="
echo "RB budgets   : $RBS"
echo "HARQs        : $HARQs"
echo "Methods      : $METHODS"
echo "ILP          : objective=$ILP_OBJECTIVE search=$ILP_RB_PEAK_SEARCH sweep-budget=${ILP_TIMEOUT}s candidate-cap=${ILP_CANDIDATE_TIMEOUT}s"
echo "Resume       : $([ "$RESUME" = "1" ] && echo "yes (RESUME=0 to redo)" || echo no)"
echo "Flowsets     : $SHARED_FLOWSETS"
echo "Output       : $SWEEP_ROOT/rb<B>/"
echo "=============================================="

status=0
for rb in $RBS; do
    tree="$SWEEP_ROOT/rb$rb"
    mkdir -p "$tree"
    # parallel_experiment.sh reads $OUTPUT_DIR/flowsets; share the one batch.
    [ -e "$tree/flowsets" ] || ln -s "$SHARED_FLOWSETS" "$tree/flowsets"
    echo ""
    echo ">>> B^max = $rb"
    resume_flag=""
    [ "$RESUME" = "1" ] && resume_flag="--resume"
    RBs="$rb" OUTPUT_DIR="$tree" ./parallel_experiment.sh --offline-only $resume_flag "$@"
    rc=$?
    [ $rc -ne 0 ] && { echo "rb=$rb: parallel_experiment.sh exited $rc" >&2; status=1; }
done

echo ""
echo "Sweep done. Reduce with:  ./sweep_experiment.sh --collect"
exit $status
