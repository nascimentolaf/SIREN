#!/bin/bash
#
# The paper's experiments, from the committed flowsets to its table, figures
# and macros: SIREN's ILP solved by siren (Gurobi, offline_phase/
# siren) and the four baselines the paper compares it with (MUST, QoS-PF, RR,
# PF).
#
#   build     planner, baselines and siren (GUROBI_HOME: Gurobi's linux64 dir)
#   sweep     every (B^max, flowset) pair: the planner, siren (rb_peak
#             objective, bounded search, 60 s per tuple, no flowset budget)
#             and the baselines, at B^max = 51, 79, 107 and 135 RBs, M = 3
#   rq4       solving time against the hyperperiod (analysis/rq4_hyperperiod.py):
#             100 flowsets schedulable at 135 RBs, their minimum-peak tuple,
#             periods stretched to H* in {0.42, 0.84, 2.52, 8.4, 25.2} s, 1 h
#             per model
#   analysis  the CSVs (analysis/csv/), then table_rq2.tex, the figures and
#             numbers_results.tex (analysis/figures/paper/)
#
# Usage (from anywhere; needs GUROBI_HOME and a Gurobi license, see
# offline_phase/siren/README.md):
#   ./run_paper_experiment.sh                     # every stage, on this machine
#   ./run_paper_experiment.sh -j 50               # 50 workers (default: one per physical core)
#   ./run_paper_experiment.sh -j 50 --shard 1/2   # machine 1 of 2 sharing this tree (a shared disk)
#   ./run_paper_experiment.sh -j 50 --shard 2/2   # machine 2
#   ./run_paper_experiment.sh --flowsets FILE     # only the flowsets FILE lists (one name per line)
#   ./run_paper_experiment.sh --stages "sweep"    # some stages only (build sweep rq4 analysis)
# A long run is best detached: nohup ./run_paper_experiment.sh ... >run.log 2>&1 &
#
# Workers. Each worker is pinned to its own physical core (the first hardware
# thread), and runs one pair at a time, the ILP and every baseline of it in
# that core: no two solvers share a core, so the per-tuple cap (wall-clock
# time) means the same everywhere. -j may not exceed the physical cores. Each
# siren process takes one Gurobi license token: with a license of W workers,
# the -j of all machines together must stay within W.
#
# Order. Pairs run flowset by flowset in the order of their numbers (the
# generator's shuffle), every budget of a flowset before the next flowset, so
# that at any time the flowsets finished at every budget are, up to the pairs
# in flight, the lowest-numbered ones: a result-blind sample of the set.
#
# Shards. With --shard K/N, the K-th machine takes the flowsets whose position
# in the list is K modulo N. Shard 1 builds (the others wait for it), runs RQ4's
# `prepare` once every shard's sweep is done, and, once every shard's RQ4
# share is done, the analysis; every shard solves its share of RQ4's models.
# Every machine must see the same tree (a shared disk).
#
# Resume. A pair is done when output/sweep/rb<B>/.parallel/status/<flowset>
# says ok (the layout of scripts/sweep_experiment.sh, which the analysis
# reads); relaunching with the same arguments redoes only the rest, and RQ4
# skips the models that have a result. A pair that failed says "fail ..." and
# is retried at the next launch.
#
# Output (OUTPUT_DIR, default output/): sweep/rb<B>/{reports,logs}/<method>/,
# rq4_siren/ (RQ4's models and results), and paper_run/ (per machine: the settings, the host's CPU, the commit,
# and pairs_<host>.tsv, one line per pair: budget, flowset, start, end, status).
# Every stage refuses a tree whose ILP reports or RQ4 results were not written
# by siren: use another OUTPUT_DIR for those of another solver.
#
# Settings (the paper's Table I; override in the environment for tests only):
#   RBS="51 79 107 135"  HARQs=3  ILP_CANDIDATE_TIMEOUT=60
#   METHODS="ilp must qospf rr pf" (ilp: the ILP of SIREN, solved by siren)
#   RQ4_SAMPLE=100  RQ4_CAP=3600  RQ4_SPLIT_H=8400  RQ4_JOBS_LARGE=12
#   (models above RQ4_SPLIT_H ms need several GB each: RQ4_JOBS_LARGE at a time)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LAUNCH_DIR="${LAUNCH_DIR:-$PWD}"
cd "$SCRIPT_DIR" || exit 1

usage() {
    awk 'NR > 2 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "$SCRIPT_DIR/run_paper_experiment.sh"
}

JOBS=""
SHARD="1/1"
FLOWSET_LIST=""
STAGES="build sweep rq4 analysis"
while [ $# -gt 0 ]; do
    case "$1" in
        -j) JOBS="${2:?-j needs a number}"; shift 2 ;;
        --shard) SHARD="${2:?--shard needs K/N}"; shift 2 ;;
        --flowsets) FLOWSET_LIST="${2:?--flowsets needs a file}"; shift 2 ;;
        --stages) STAGES="${2:?--stages needs a list}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1 (--help)" >&2; exit 2 ;;
    esac
done
[[ "$SHARD" =~ ^([0-9]+)/([0-9]+)$ ]] && [ "${BASH_REMATCH[1]}" -ge 1 ] &&
    [ "${BASH_REMATCH[1]}" -le "${BASH_REMATCH[2]}" ] ||
    { echo "--shard $SHARD: expected K/N with 1 <= K <= N" >&2; exit 2; }
SHARD_K=${BASH_REMATCH[1]}
SHARD_N=${BASH_REMATCH[2]}
for s in $STAGES; do
    case " build sweep rq4 analysis " in *" $s "*) ;; *) echo "Unknown stage: $s" >&2; exit 2 ;; esac
done
has_stage() { case " $STAGES " in *" $1 "*) return 0 ;; esac; return 1; }
if [ -n "$FLOWSET_LIST" ]; then
    case "$FLOWSET_LIST" in /*) ;; *) FLOWSET_LIST="$LAUNCH_DIR/$FLOWSET_LIST" ;; esac
    [ -r "$FLOWSET_LIST" ] || { echo "Cannot read --flowsets $FLOWSET_LIST" >&2; exit 2; }
fi

# The paper's settings.
RBS="${RBS:-51 79 107 135}"
export HARQs="${HARQs:-3}"
export METHODS="${METHODS:-ilp must qospf rr pf}"
export ILP_OBJECTIVE=rb_peak ILP_RB_PEAK_SEARCH=bounded ILP_TIMEOUT=0
export ILP_CANDIDATE_TIMEOUT="${ILP_CANDIDATE_TIMEOUT:-60}"
RQ4_SAMPLE="${RQ4_SAMPLE:-100}"
RQ4_CAP="${RQ4_CAP:-3600}"
RQ4_SPLIT_H="${RQ4_SPLIT_H:-8400}"
RQ4_JOBS_LARGE="${RQ4_JOBS_LARGE:-12}"
RQ4_RB=135                                   # the budget whose tables RQ4 times
POLL="${POLL:-60}"                           # seconds between checks while waiting

source ./parameters.sh                       # paths, TTI, t_up, A^max, ... (unchanged)
SIREN_DIR="$DIR/offline_phase/siren"
SWEEP="$OUTPUT_DIR/sweep"
# A folder of its own, which the rq4 stage prepares from this sweep.
RQ4_DIR="$OUTPUT_DIR/rq4_siren"
RUN_DIR="$OUTPUT_DIR/paper_run"
HOST="$(hostname -s)"
TTI_MS=$(echo "$TTI * 1000" | bc -l)
mkdir -p "$RUN_DIR"

say() { echo "[$(date '+%F %T')] [$HOST $SHARD] $*"; }
die() { say "ERROR: $*" >&2; exit 1; }

# One worker per physical core, on its first hardware thread, spread evenly
# over the cores when -j is smaller.
mapfile -t CORES < <(lscpu -p=CPU,CORE | grep -v '^#' | awk -F, '!seen[$2]++ { print $1 }')
[ ${#CORES[@]} -gt 0 ] || die "no CPU list from lscpu"
JOBS="${JOBS:-${#CORES[@]}}"
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "-j $JOBS: expected a positive number"
[ "$JOBS" -le ${#CORES[@]} ] || die "-j $JOBS: more workers than the ${#CORES[@]} physical cores"
WORKER_CPUS=()
for ((w = 0; w < JOBS; w++)); do WORKER_CPUS+=("${CORES[$((w * ${#CORES[@]} / JOBS))]}"); done
CPU_LIST=$(IFS=,; echo "${WORKER_CPUS[*]}")

# The flowsets, in number order: all of output/flowsets, or those FILE lists.
mapfile -t ALL_FLOWSETS < <(
    if [ -n "$FLOWSET_LIST" ]; then
        grep -oE 'flowset_[0-9]+' "$FLOWSET_LIST"
    else
        ls "$FLOWSETS_DIR" | grep -oE '^flowset_[0-9]+(\.json)?$' | sed 's/\.json$//'
    fi | sort -u)
[ ${#ALL_FLOWSETS[@]} -gt 0 ] || die "no flowsets"
for fs in "${ALL_FLOWSETS[@]}"; do
    [ -r "$FLOWSETS_DIR/$fs.json" ] || die "no $FLOWSETS_DIR/$fs.json"
done
MY_FLOWSETS=()
for i in "${!ALL_FLOWSETS[@]}"; do
    [ $((i % SHARD_N)) -eq $((SHARD_K - 1)) ] && MY_FLOWSETS+=("${ALL_FLOWSETS[$i]}")
done

status_of() { cat "$SWEEP/rb$1/.parallel/status/$2" 2>/dev/null; }

# ------------------------------------------------------------------------
# Manifest: what ran, where, on which code.
# ------------------------------------------------------------------------
{
    echo "started   : $(date '+%F %T %Z')"
    echo "host      : $HOST ($(lscpu | sed -n 's/^Model name: *//p' | head -1); ${#CORES[@]} cores)"
    echo "shard     : $SHARD, $JOBS worker(s) on CPUs $CPU_LIST"
    echo "stages    : $STAGES"
    echo "flowsets  : ${#ALL_FLOWSETS[@]} (${FLOWSET_LIST:-all of $FLOWSETS_DIR}), ${#MY_FLOWSETS[@]} in this shard"
    echo "budgets   : $RBS RBs; M = $HARQs; methods: $METHODS"
    echo "ILP       : objective $ILP_OBJECTIVE, search $ILP_RB_PEAK_SEARCH, $ILP_CANDIDATE_TIMEOUT s per tuple, no flowset budget"
    echo "RQ4       : $RQ4_SAMPLE flowsets at $RQ4_RB RBs, $RQ4_CAP s per model"
    echo "TTI       : $(LC_ALL=C printf %g "$TTI_MS") ms, t_up $UPPER_LAYER_DELAY s, A^max $A_MAX"
    echo "commit    : $(git -C "$DIR" rev-parse HEAD 2>/dev/null || echo '?')$(git -C "$DIR" status --porcelain 2>/dev/null | grep -q . && echo ' (uncommitted changes)')"
} >"$RUN_DIR/manifest_${HOST}.txt"
say "$(sed -n 's/^shard *: //p' "$RUN_DIR/manifest_${HOST}.txt")"

# ------------------------------------------------------------------------
# build
# ------------------------------------------------------------------------
BUILT="$RUN_DIR/built"
build() {
    [ -n "${GUROBI_HOME:-}" ] || die "GUROBI_HOME is not set (siren needs Gurobi's linux64 directory)"
    say "build: planner, baselines, siren"
    rm -f "$BUILT"
    (cd "$PLANNER_DIR" && make) >"$RUN_DIR/build_planner.log" 2>&1 || die "planner build failed ($RUN_DIR/build_planner.log)"
    (cd "$BASELINE_ALGORITHMS_DIR" && make) >"$RUN_DIR/build_baselines.log" 2>&1 || die "baselines build failed ($RUN_DIR/build_baselines.log)"
    (cd "$SIREN_DIR" && make) >"$RUN_DIR/build_siren.log" 2>&1 || die "siren build failed ($RUN_DIR/build_siren.log)"
    date '+%F %T' >"$BUILT"
}

wait_for() {  # wait_for <what> <command...>: until the command succeeds
    local what="$1" n=0
    shift
    until "$@"; do
        [ $((n % 10)) -eq 0 ] && say "waiting for $what"
        n=$((n + 1))
        sleep "$POLL"
    done
}

if has_stage build; then
    if [ "$SHARD_K" -eq 1 ]; then build; else wait_for "shard 1's build" test -e "$BUILT"; fi
fi
for b in "$PLANNER_DIR/planner" "$SIREN_DIR/siren" $(for m in $METHODS; do
        [ "$m" = ilp ] || echo "$BASELINE_ALGORITHMS_DIR/$m/$m"; done); do
    [ -x "$b" ] || die "$b is not built (stage build)"
done

# ------------------------------------------------------------------------
# sweep
# ------------------------------------------------------------------------
check_tree() {  # refuse a sweep tree whose ILP reports siren did not write
    local f solver
    f=$(find "$SWEEP" -path '*/reports/ilp/*/offline_phase_report.json' -print -quit 2>/dev/null)
    [ -n "$f" ] || return 0
    solver=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["config"].get("solver", ""))' "$f")
    case "$solver" in
        gurobi*) return 0 ;;
        *) die "$SWEEP holds ILP reports not written by siren ($f: solver '${solver:-none}'): use another OUTPUT_DIR or a clone" ;;
    esac
}

run_method() {  # run_method <rb> <flowset> <method>
    local rb="$1" fs="$2" method="$3"
    local out="$SWEEP/rb$rb/reports/$method/$fs" log="$SWEEP/rb$rb/logs/$method/$fs.log"
    local flowset="$FLOWSETS_DIR/$fs.json"
    rm -rf "$out"
    mkdir -p "$out" "$(dirname "$log")"
    (
        echo "=== $fs / $method ==="
        echo "Host: $HOST"               # plot_paper.py --derive reads it
        echo "Output: $out"
        echo ""
        if [ "$method" = ilp ]; then
            (cd "$PLANNER_DIR" && ./planner "$flowset" "$MCS_CONFIG" "$out/planner_report.json") || exit 1
            (cd "$SIREN_DIR" && ./siren "$out/planner_report.json" \
                --n-rb "$rb" --m-harq "$HARQs" --tti "$TTI_MS" \
                --objective "$ILP_OBJECTIVE" --rb-peak-search "$ILP_RB_PEAK_SEARCH" \
                --timeout "$ILP_TIMEOUT" --candidate-timeout "$ILP_CANDIDATE_TIMEOUT" \
                --output-scheduling "$out/offline_phase_report.json" --schedule-dir "$out/") || exit 1
        else
            # One schedule per flowset, combo_0000.json; RBs and the other
            # settings reach the binary through the environment.
            (cd "$BASELINE_ALGORITHMS_DIR/$method" &&
                RBs="$rb" "./$method" "$flowset" "$MCS_CONFIG" "$out/combo_0000.json") || exit 1
        fi
    ) >"$log" 2>&1
}

run_pair() {  # run_pair <rb> <flowset>: every method, then the status file
    local rb="$1" fs="$2" method failed="" start st="$SWEEP/rb$1/.parallel/status"
    start=$(date +%s)
    mkdir -p "$st"
    rm -f "$st/$fs"
    for method in $METHODS; do
        run_method "$rb" "$fs" "$method" || failed="$failed $method"
    done
    if [ -z "$failed" ]; then
        echo ok >"$st/.$fs.$HOST" && mv "$st/.$fs.$HOST" "$st/$fs"
    else
        echo "fail$failed" >"$st/.$fs.$HOST" && mv "$st/.$fs.$HOST" "$st/$fs"
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "rb$rb" "$fs" "$start" "$(date +%s)" "$(status_of "$rb" "$fs")" \
        >>"$RUN_DIR/pairs_${HOST}.tsv"
}

sweep() {
    check_tree
    local queue lock next rb fs w n pids=()
    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    queue="$WORK/queue"; lock="$WORK/lock"; next="$WORK/next"
    for fs in "${MY_FLOWSETS[@]}"; do
        for rb in $RBS; do
            [ "$(status_of "$rb" "$fs")" = ok ] || echo "$rb $fs"
        done
    done >"$queue"
    n=$(grep -c . "$queue")
    say "sweep: $n pair(s) to run in this shard ($((${#MY_FLOWSETS[@]} * $(wc -w <<<"$RBS")))" \
        "in all), $JOBS at a time"
    echo 1 >"$next"
    for ((w = 0; w < JOBS; w++)); do
        (
            taskset -p -c "${WORKER_CPUS[$w]}" $BASHPID >/dev/null || exit 1
            while :; do
                pair=$(flock -x "$lock" bash -c 'i=$(<"$1"); sed -n "${i}p" "$2"; echo $((i + 1)) >"$1"' _ "$next" "$queue")
                [ -n "$pair" ] || exit 0
                run_pair $pair
            done
        ) &
        pids+=($!)
    done
    for pid in "${pids[@]}"; do wait "$pid"; done
    local failed
    failed=$(for fs in "${MY_FLOWSETS[@]}"; do for rb in $RBS; do
        case "$(status_of "$rb" "$fs")" in ok) ;; *) echo "rb$rb/$fs" ;; esac; done; done)
    [ -z "$failed" ] || die "sweep: $(grep -c . <<<"$failed") pair(s) not ok, e.g. $(head -3 <<<"$failed" | tr '\n' ' '); relaunch to retry"
    say "sweep: this shard's pairs are done"
}

all_pairs_ok() {  # every pair of every shard
    local fs rb
    for fs in "${ALL_FLOWSETS[@]}"; do
        for rb in $RBS; do [ "$(status_of "$rb" "$fs")" = ok ] || return 1; done
    done
}

has_stage sweep && sweep

# ------------------------------------------------------------------------
# rq4
# ------------------------------------------------------------------------
RQ4_PREPARED="$RUN_DIR/rq4_prepared"
check_rq4_dir() {  # refuse RQ4 results that siren did not solve
    local f solver
    f=$(find "$RQ4_DIR" -name 'H*.result.json' -print -quit 2>/dev/null)
    [ -n "$f" ] || return 0
    solver=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("solver", ""))' "$f")
    [ "$solver" = siren ] || die "$RQ4_DIR holds results not solved by siren ($f: solver '${solver:-none}')"
}
rq4_all_done() {  # every prepared model has a result
    local n_inst n_res
    n_inst=$(find "$RQ4_DIR" -path '*/flowset_*/H*.json' -regex '.*/H[0-9]+\.json' | wc -l)
    n_res=$(find "$RQ4_DIR" -path '*/flowset_*/H*.result.json' | wc -l)
    [ "$n_inst" -gt 0 ] && [ "$n_res" -ge "$n_inst" ]
}
rq4() {
    local rc=0
    check_tree
    check_rq4_dir
    wait_for "every shard's sweep" all_pairs_ok
    if [ "$SHARD_K" -eq 1 ]; then
        if [ ! -e "$RQ4_PREPARED" ]; then
            say "rq4: prepare ($RQ4_SAMPLE flowsets schedulable at $RQ4_RB RBs)"
            rm -rf "${RQ4_DIR:?}"        # an earlier prepare that did not finish
            (cd "$DIR" && python3 analysis/rq4_hyperperiod.py --out "$RQ4_DIR" prepare \
                --reports "$SWEEP/rb$RQ4_RB/reports/ilp" --sample "$RQ4_SAMPLE") \
                >"$RUN_DIR/rq4_prepare.log" 2>&1 || die "rq4 prepare failed ($RUN_DIR/rq4_prepare.log)"
            date '+%F %T' >"$RQ4_PREPARED"
        fi
    else
        wait_for "shard 1's rq4 prepare" test -e "$RQ4_PREPARED"
    fi
    say "rq4: this shard's models, $JOBS at a time up to H* = $RQ4_SPLIT_H ms, then $RQ4_JOBS_LARGE"
    (cd "$DIR" &&
        taskset -c "$CPU_LIST" python3 analysis/rq4_hyperperiod.py --out "$RQ4_DIR" run \
            --solver "$SIREN_DIR/siren" -j "$JOBS" --max-h "$RQ4_SPLIT_H" --cap "$RQ4_CAP" \
            --harqs "$HARQs" --shard "$SHARD" &&
        taskset -c "$CPU_LIST" python3 analysis/rq4_hyperperiod.py --out "$RQ4_DIR" run \
            --solver "$SIREN_DIR/siren" -j "$RQ4_JOBS_LARGE" --cap "$RQ4_CAP" \
            --harqs "$HARQs" --shard "$SHARD") >>"$RUN_DIR/rq4_run_${HOST}.log" 2>&1 || rc=1
    [ $rc -eq 0 ] || die "rq4 run failed ($RUN_DIR/rq4_run_${HOST}.log)"
    say "rq4: this shard's models are done"
}

has_stage rq4 && rq4

# ------------------------------------------------------------------------
# analysis
# ------------------------------------------------------------------------
analysis() {
    local a="$DIR/analysis" f
    # plot_paper.py reads output/sweep/ and writes analysis/: no other tree.
    [ "$OUTPUT_DIR" = "$DIR/output" ] ||
        die "analysis: needs OUTPUT_DIR=$DIR/output (plot_paper.py reads output/sweep/), not $OUTPUT_DIR"
    wait_for "every shard's sweep" all_pairs_ok
    wait_for "every shard's RQ4 models" rq4_all_done
    check_tree
    check_rq4_dir
    say "analysis: CSVs, table, figures and macros"
    # From empty: --derive keeps the rows of whatever its reports lack.
    for f in "$a"/csv/output_rb*.csv "$a/csv/output_sweep.csv" "$a/csv/derived_baseline_configs.csv" \
             "$a/csv/derived_ilp_timing_rb135.csv" "$a/csv/rq4_hyperperiod.csv" "$a/csv/rq4_control.csv" \
             "$a"/figures/paper/*; do
        rm -f "$f"
    done
    {
        RBS="$RBS" ./sweep_experiment.sh --collect &&
            (cd "$DIR" && python3 analysis/rq4_hyperperiod.py --out "$RQ4_DIR" collect &&
                python3 analysis/plot_paper.py --derive && python3 analysis/plot_paper.py)
    } >"$RUN_DIR/analysis.log" 2>&1 || die "analysis failed ($RUN_DIR/analysis.log)"
    say "analysis: analysis/figures/paper/ (table_rq2.tex, fig_*.pdf, numbers_results.tex)"
}

if has_stage analysis; then
    if [ "$SHARD_K" -eq 1 ]; then analysis; else say "analysis: left to shard 1"; fi
fi
say "done"
