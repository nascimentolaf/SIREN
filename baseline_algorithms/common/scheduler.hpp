#ifndef SCHEDULER_HPP
#define SCHEDULER_HPP

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cctype>
#include <deque>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <queue>
#include <random>
#include <stdexcept>
#include <tuple>
#include <utility>
#include <map>
#include <set>
#include <sstream>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>
#include <iostream>
#include <fstream>
#include <set>

#include "qos_data.h"
#include "json.hpp"
#include "urllc_config.hpp"
using json = nlohmann::json;

constexpr std::uint64_t kRandomSeed = 42; // Fixed for reproducible thesis experiments.
constexpr double kTimeEpsMs = 1e-9;
constexpr double kPeriodScale = 1'000'000.0; // microseconds per millisecond.

struct RuntimeConfig
{
    double tti_s{};
    double rbs{};
    int harqs{};
    // HARQs=auto: the scheduler sizes the HARQ pool itself, large enough
    // that it never limits the schedule; harqs stays 0 until it does. Only
    // a scheduler that asks for it (read_runtime_config(true)) accepts auto.
    bool harqs_auto{false};
    double upper_layer_delay_s{};
    double feedback_per_ret_s{};
    double sinr_db{10.0};
    std::size_t rlc_header_bytes{8};
    double pf_alpha{0.1};
    double pf_initial_throughput_bytes_per_ms{1.0};
    int a_max{4}; // A^max, the layer-wide cap on transmission attempts

    // How long a transport block actually occupies its HARQ process.
    //
    // "wcet"    : always A_i attempts, the worst case Eq. (5) budgets for.
    // "sampled" : the attempt count a run would really see -- a TB stops as
    //             soon as it decodes, so it usually finishes well before its
    //             WCTT. Non-preemptive work-conserving scheduling is not
    //             sustainable with respect to execution-time reduction, so a
    //             schedule that meets every deadline at WCTT can miss one
    //             when jobs finish early. This mode is what exposes that; a
    //             table-driven schedule is immune, since its start times are
    //             fixed offline and an early finish just idles the process.
    std::string exec_time_mode{"wcet"};
    std::uint64_t exec_sample_seed{42};
};

inline bool sampling_execution_times(const RuntimeConfig &cfg)
{
    return cfg.exec_time_mode == "sampled";
}

// Draws how many HARQ attempts a transport block really needs: each attempt
// fails independently with probability `bler`, and the count is capped at
// a_max because the process is released after the last attempt either way.
//
// Seeded per (run, job) rather than from a shared stream, so a job's draw does
// not depend on how many jobs happened to be drawn before it. That keeps one
// method's sample identical to another's for the same seed, which is the
// whole point: the methods must face the same run, not merely the same
// distribution.
class AttemptSampler
{
public:
    AttemptSampler(std::uint64_t seed, bool enabled) : seed_(seed), enabled_(enabled) {}

    int Draw(double bler, int a_max, std::uint64_t job_id) const
    {
        if (!enabled_ || a_max <= 1)
            return a_max;

        std::mt19937_64 rng(seed_ ^ (job_id * 0x9E3779B97F4A7C15ULL));
        std::uniform_real_distribution<double> uniform(0.0, 1.0);
        for (int attempt = 1; attempt < a_max; ++attempt)
        {
            if (uniform(rng) >= bler)
                return attempt; // decoded on this attempt
        }
        return a_max;
    }

    bool enabled() const { return enabled_; }

private:
    std::uint64_t seed_;
    bool enabled_;
};

// Raised when a flow admits no transmission configuration at all (Eq. (6):
// P_i is empty). That makes the flow set infeasible, which is a schedulability
// verdict, not a tool failure -- a sweep has to be able to record it as
// unschedulable rather than lose the data point to a non-zero exit status.
// The ILP path reports the same condition through `infeasible_flows`.
struct InfeasibleFlowSet : std::runtime_error
{
    std::vector<std::string> apps;

    explicit InfeasibleFlowSet(std::string app, const std::string &why)
        : std::runtime_error(why), apps{std::move(app)} {}
};

struct Flow
{
    std::string app;                 //[cite: 1]
    int deadline_ms{};               //[cite: 1]
    double error{};                  //[cite: 1]
    std::size_t packet_size_bytes{}; //[cite: 1]
    double period_ms{};              //[cite: 1]
    std::string type;                //[cite: 1]
};

struct FlowScheduleConfig
{
    int tbs{};                    //[cite: 1]
    int m{};                      //[cite: 1]
    int b{};                      //[cite: 1]
    int k{};                      // A_i(K): attempts required by Eq. (4)
    double bler{};                // per-attempt BLER of the chosen MCS row
    double expected_c_ms{};       //[cite: 1]
    int num_segments{};           //[cite: 1]
    std::vector<int> segment_tbs; //[cite: 1]
};

struct Job
{
    std::size_t flow_index{};
    int attempts_wcet{}; // A_i(K), what Eq. (5) budgets for
    std::size_t instance_index{};
    int segment_index{};
    int tbs{};
    int m{};
    int b{};
    int k{};
    double bler{};
    int release_ms{};
    int deadline_ms{};
    bool schedulable{true};
};

// Time units. Every time parameter that crosses a component boundary is in
// milliseconds: the catalogue (period, deadline), the planner's C, this
// struct and the reports. The scheduling grid is the TTI ($TTI seconds,
// 0.25 ms in the paper); simulators step in TTIs and convert with
// to_tti(ms) = ms / tti_ms. The times below are *integer* milliseconds, so a
// baseline's dispatch events must fall on whole milliseconds. That holds
// because the catalogue's periods, deadlines and TB durations are integer
// milliseconds (scripts/check_catalogue.py enforces it), and whole_ms() below
// makes any violation fail loudly instead of truncating a sub-millisecond
// event.
struct DispatchEntry
{
    std::string app;   //[cite: 1]
    int b{};           //[cite: 1]
    int deadline_ms{}; //[cite: 1]
    int end_ms{};      //[cite: 1]
    int machine{};     //[cite: 1]
    int release_ms{};  //[cite: 1]
    int start_ms{};    //[cite: 1]
    int subtask{};     //[cite: 1]
};

[[noreturn]] void die(const std::string &message);

// Narrow a millisecond time to the integer milliseconds of DispatchEntry,
// refusing silently to drop a fraction of a millisecond.
inline int whole_ms(double ms, const char *what)
{
    const double r = std::round(ms);
    if (std::abs(ms - r) > 1e-6)
        die(std::string(what) + " = " + std::to_string(ms) +
            " ms is not a whole millisecond; DispatchEntry stores integer milliseconds");
    return static_cast<int>(r);
}

struct ReadyItem
{
    int flow_idx;
    int release_ms;
    int deadline_ms; // absolute
    int subtask;
    // Per job, because under EXEC_TIME_MODE=sampled two jobs of the same flow
    // need not take the same number of attempts.
    double duration_ms;
};

struct MachineState
{
    bool busy = false;
    double free_at = 0.0;
    int b_in_use = 0;
};

struct ResolvedFlow
{
    std::string app;
    int deadline;  // ms
    double period; // ms
    double error;
    double packetsize; // bytes
    int num_segments;
    int tbs;
    int m;
    int k;
    int b;
    double bler_used = 0.0; // per-attempt BLER of the chosen MCS row
    double c;               // ms, worst-case transmission time of a single TB
    int fiveQI = -1;
    double priority_level = 0.0;    // raw 3GPP value (lower = higher priority)
    double priority_scalar = 0.0;   // normalized P_i, filled in after all flows resolved
    double required_rate_bps = 0.0; // implied GBR target, bits/ms
};

// ---------------------------------------------------------------------
// Input data structures (identical to the EDF/QoS-PF schedulers)
// ---------------------------------------------------------------------

struct FlowSpec
{
    std::string app;
    int deadline;      // ms, relative to release
    double error;      // target BLER
    double packetsize; // bytes
    int period;        // ms
    std::string type;
    int fiveQI = -1; // 3GPP 5QI declared by the app database ("5qi"), or -1
};

std::vector<FlowSpec> parse_flowset(const json &j);
std::vector<urllc::TbsTable> parse_mcs_table(const json &j);
std::vector<urllc::TbsTable> parse_mcs_table(const std::string &path, double requested_sinr_db);

// Section V, applied identically by every method in this framework.
//
// Runs Eqs. (2)-(6) for the flow without the C + t_up <= D screen and returns
// one configuration, among those fitting the cell (B <= B^max): fewest
// transport blocks, then fewest HARQ attempts, then fewest resource blocks,
// then the higher MCS to break ties. Only configurations within the
// layer-wide attempt cap (A <= A^max) compete when any fits the cell; the
// rule falls back to more attempts only when none does (the analysis then
// counts the run outside the model). Fixing the rule here is what keeps the
// comparison about scheduling policy -- round-robin in particular is defined
// as channel-independent, so it has no business picking a better
// configuration than the others.
//
// Before this existed each method rolled its own, and none of them applied
// Eq. (3): they fed the packet-level target E_i straight in as a per-TB
// target, so every flow with N > 1 was allowed a configuration that misses
// its own reliability requirement -- a materially easier problem than the one
// the ILP solves.
//
// $FLOW_CONFIG overrides the choice: point it at a JSON object mapping each
// application to one urllc::Candidate (tbs, m, segments, attempts, rbs,
// etb_target, bler, wctt_ms) and the method schedules that configuration
// instead of picking its own. This is what lets a baseline be compared with
// the ILP per configuration rather than per flow set -- the ILP decides every
// candidate the planner emits, a baseline normally only ever sees the one it
// chose. analysis/sweep_configurations.py drives it. Unset, nothing changes.
urllc::Candidate select_flow_config(
    std::uint32_t packet_bytes, double packet_error, double deadline_ms,
    const std::string &app, const std::vector<urllc::TbsTable> &tables,
    const RuntimeConfig &runtime);

std::string default_output_path(const std::string &flowset_path, const std::string &scheduler);

// Occupancy of one transport block: the k HARQ rounds the MCS table
// prescribes for the flow's error target, k * (TTI + FEEDBACK_PER_RET). This
// is the framework's transmission-time model and every scheduler uses it, so
// a comparison between them isolates the scheduling policy rather than the
// timing assumptions. It does not include the upper-layer delay, below.
double tb_duration_ms(int k, const RuntimeConfig &cfg);

// t_up, UPPER_LAYER_DELAY: the time a packet spends in the other layers. It
// holds neither a HARQ process nor RBs, but a packet is delivered only t_up
// after its last TB ends, so that TB has to end t_up before the packet's
// deadline. In ms, and in whole TTIs rounded up (the reports' t_up_tti: a
// packet is late when its last end_tti + t_up_tti > deadline_tti).
double upper_layer_delay_ms(const RuntimeConfig &cfg);
long long upper_layer_delay_tti(const RuntimeConfig &cfg);

// RLC header overhead per segment, in bytes, from $RLC_HEADER_BYTES.
//
// The single source of truth for the whole framework: every scheduler and the
// offline planner read it from here (or from RuntimeConfig::rlc_header_bytes,
// which this fills in), so changing scripts/parameters.sh changes the
// segmentation everywhere at once. Defaults to 8 when unset, which is what
// the value used to be hardcoded to.
std::size_t rlc_header_bytes();

std::vector<Flow> parse_flowset(const std::string &path);

double compute_hyperperiod_ms(const std::vector<Flow> &flows);

void write_report(const std::string &path, const json &report);

// Writes the "this flow set cannot be transmitted at all" verdict, in the same
// combo-style shape as a real report but with an empty dispatch table, so the
// analysis layer sees a KO rather than a missing file.
void write_infeasible_report(const std::string &path, int combo_index,
                             const RuntimeConfig &runtime,
                             const std::vector<std::string> &apps,
                             const std::string &objective,
                             const std::string &reason);

double require_number(const json &obj, const char *key, const std::string &where);
std::size_t require_nonnegative_size(const json &obj, const char *key, const std::string &where);

// allow_auto_harqs: accept HARQs=auto (see RuntimeConfig::harqs_auto).
// Every other scheduler needs a fixed number of HARQ processes and rejects it.
RuntimeConfig read_runtime_config(bool allow_auto_harqs = false);

std::vector<FlowScheduleConfig> build_flow_configs(
    const std::vector<Flow> &flows, const RuntimeConfig &runtime,
    const std::vector<urllc::TbsTable> &tables);

// `objective` names the method writing the report, the way the `objective`
// argument of write_infeasible_report above does, so a report says which
// scheduler produced it whether it succeeded or was infeasible. It has no
// default on purpose: this used to be hardcoded to "proportional_fair", which
// left every method that did not overwrite it afterwards labelled as pf.
json make_report(
    int combo_index,
    const RuntimeConfig &runtime,
    const std::vector<Flow> &flows,
    const std::vector<FlowScheduleConfig> &flow_cfgs,
    const std::vector<DispatchEntry> &dispatch,
    double hyperperiod_ms,
    const std::string &objective);

std::vector<Job> materialize_jobs(
    const std::vector<Flow> &flows, const std::vector<FlowScheduleConfig> &flow_cfgs,
    double hyperperiod_ms, const RuntimeConfig &runtime);

int extract_combo_index(const std::string &flowset_path);

#endif
