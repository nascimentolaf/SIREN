// qospf.cpp
//
// QoS-aware Proportional Fairness (QoS-PF) scheduler for the industrial-5G
// real-time flowset benchmark, split from the supplied implementation.
//
// The supplied source ended after compute_hyperperiod(); it did not include
// a main() or a complete dispatch loop, so no missing scheduler logic is
// invented here.

#include "qospf.hpp"

std::string require_env(const std::string &name)
{
    const char *v = std::getenv(name.c_str());
    if (v == nullptr || std::string(v).empty())
    {
        throw std::runtime_error("Missing required environment variable: " + name);
    }
    return std::string(v);
}

double require_env_double(const std::string &name)
{
    return std::stod(require_env(name));
}

int require_env_int(const std::string &name)
{
    return std::stoi(require_env(name));
}

// Section V, delegated to the shared implementation so that this scheduler and
// every other method resolve a flow to the exact same configuration -- see
// select_flow_config() in common/scheduler.cpp. What used to live here picked
// a row straight off the packet-level error target, skipping Eq. (3)
// entirely, so a flow segmented into N transport blocks was allowed a
// configuration that misses its own reliability requirement by a factor of N.
ResolvedFlow resolve_flow(const FlowSpec &flow, const std::vector<urllc::TbsTable> &table,
                          const RuntimeConfig &runtime)
{
    if (table.empty())
    {
        throw std::runtime_error("MCS table is empty");
    }

    const auto c = select_flow_config(
        static_cast<std::uint32_t>(flow.packetsize), flow.error,
        static_cast<double>(flow.deadline), flow.app, table, runtime);

    ResolvedFlow rf;
    rf.app = flow.app;
    rf.deadline = flow.deadline;
    rf.period = flow.period;
    rf.error = flow.error;
    rf.packetsize = flow.packetsize;
    rf.num_segments = c.segments;
    rf.tbs = c.tbs;
    rf.m = c.m;
    rf.k = c.attempts;
    rf.b = c.rbs;
    rf.bler_used = c.bler;
    rf.c = c.wcttMs;

    // G_i(t) in Eq. 2 is measured against the flow's Guaranteed Bit Rate.
    // GBR is a *sustained* rate in 3GPP, so it is the payload spread over the
    // period, not over the deadline: one packet per period is exactly what
    // the flow is guaranteed. Dividing by the deadline instead overstates it
    // by period/deadline (4x for a 10 ms deadline on a 40 ms period), which
    // pinned G near 1 for every flow and left the beta term inert.
    rf.required_rate_bps = (flow.packetsize * 8.0) / flow.period; // bits/ms

    rf.fiveQI = flow.fiveQI; // -1 when the flowset does not declare one
    return rf;
}

// Resolve the 3GPP QoS class that supplies P_i(t) in Eq. 2.
//
// The paper assumes every flow arrives already carrying its QFI/5QI, so when
// the app database declares one ("5qi") it is used directly and nothing is
// inferred. That is the intended path: the priority a flow gets is then a
// stated property of the service, not a by-product of a distance metric.
//
// Otherwise fall back to matching (deadline, error) against the table. The
// previous fallback scored rows by
//     |packet_delay_budget - deadline| + |error_exp - target_exp|
// which adds milliseconds to a log10 exponent. Delay budgets span 5-500 ms
// and exponents -2..-8, so the delay term swamped the error term and flows
// three orders of magnitude apart in reliability landed on the same 5QI --
// a 10 ms / 1e-5 flow was handed an eMBB row at priority level 68 while a
// 10 ms / 1e-4 flow got Discrete Automation at 19.
//
// The repaired version scores delay relatively (so both terms are
// dimensionless) and, ahead of distance, prefers rows that actually satisfy
// the flow: a delay budget no looser than the deadline and an error rate no
// looser than the target.
void assign_5qi(ResolvedFlow &rf, const qos_data &qd)
{
    if (rf.fiveQI >= 0)
    {
        for (const auto &row : qd.QoS5GTable)
        {
            if (row.fiveQI == rf.fiveQI)
            {
                rf.priority_level = row.default_priority_level;
                return;
            }
        }
        throw std::runtime_error("app " + rf.app + " declares 5QI " +
                                 std::to_string(rf.fiveQI) +
                                 ", which is not in the 3GPP table");
    }

    const double target_exp = std::log10(rf.error);
    const double deadline = std::max(static_cast<double>(rf.deadline), 1e-9);

    const qos_data::QoS5G *best = nullptr;
    bool best_satisfies = false;
    double best_dist = std::numeric_limits<double>::infinity();

    for (const auto &row : qd.QoS5GTable)
    {
        const double delay_dist =
            std::abs(row.packet_delay_budget - rf.deadline) / deadline;
        const double error_dist =
            std::abs(row.packet_error_rate_exp - target_exp);
        const double dist = delay_dist + error_dist;

        const bool satisfies = (row.packet_delay_budget <= rf.deadline) &&
                               (row.packet_error_rate_exp <= target_exp + 1e-9);

        // A row that meets both requirements always beats one that does not.
        if (best == nullptr ||
            (satisfies && !best_satisfies) ||
            (satisfies == best_satisfies && dist < best_dist))
        {
            best = &row;
            best_satisfies = satisfies;
            best_dist = dist;
        }
    }

    if (best != nullptr)
    {
        rf.fiveQI = best->fiveQI;
        rf.priority_level = best->default_priority_level;
    }
    else
    {
        rf.fiveQI = -1;
        rf.priority_level = 0.0;
    }
}

// Normalize priority_level across the flowset into P_i in [0, 1], where
// lower 3GPP priority_level (higher real-world priority) maps to a higher
// P_i.
void normalize_priorities(std::vector<ResolvedFlow> &flows)
{
    if (flows.empty()) return;
    double min_l = flows.front().priority_level;
    double max_l = flows.front().priority_level;
    for (const auto &f : flows)
    {
        min_l = std::min(min_l, f.priority_level);
        max_l = std::max(max_l, f.priority_level);
    }
    for (auto &f : flows)
    {
        if (max_l > min_l)
        {
            f.priority_scalar = (max_l - f.priority_level) / (max_l - min_l);
        }
        else
        {
            f.priority_scalar = 1.0;
        }
    }
}

// ---------------------------------------------------------------------
// Discrete-event QoS-PF simulation
// ---------------------------------------------------------------------

long long ms_to_int(double ms)
{
    return std::llround(ms);
}

long long compute_hyperperiod(const std::vector<ResolvedFlow> &flows)
{
    long long h = 1;
    for (const auto &f : flows)
    {
        long long p = ms_to_int(f.period);
        h = std::lcm(h, p);
    }
    return h;
}
