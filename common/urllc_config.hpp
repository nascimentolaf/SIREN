#ifndef URLLC_CONFIG_HPP
#define URLLC_CONFIG_HPP

// ---------------------------------------------------------------------------
// Transmission-configuration selection, Section V of the paper.
//
// One implementation of Eqs. (2)-(6), shared by the offline planner and by
// every baseline scheduler, so that a flow's reliability and resource demand
// cannot differ between methods. Before this header existed the planner
// applied Eq. (3) (rounded to a decade) while the baselines skipped it
// entirely and fed the packet-level target straight in as a per-TB target,
// which made the baselines solve a materially easier problem.
//
// Deliberately free of any JSON dependency: the planner, the ILP and the
// baselines each bundle a different nlohmann/json, so parsing stays in the
// callers and only the arithmetic is shared.
// ---------------------------------------------------------------------------

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

namespace urllc
{

// One (TB size, MCS) row of the link-layer table: the per-attempt block error
// rate E-bar^tb_{k,m} of the paper and the resource blocks the row consumes.
struct McsRow
{
    int m = 0;        // MCS index
    double bler = 0.0; // per-attempt error rate of the whole TB, as simulated (Eq. (4))
    int b = 0;        // resource blocks per transmission attempt
};

struct TbsTable
{
    int tbs = 0;       // transport block size, bytes
    double sinr = 0.0; // dB
    std::vector<McsRow> mcs;
};

struct FlowSpec
{
    std::uint32_t packetBytes = 0;
    double packetError = 0.0; // E_i
    double deadlineMs = 0.0;  // D_i
};

// A configuration K = (k, m) that survived every necessary condition.
struct Candidate
{
    int tbs = 0;
    int m = 0;
    int segments = 0;     // N_i(K), Eq. (2)
    int attempts = 0;     // A_i(K), Eq. (4)
    int rbs = 0;          // B_i(K)
    double etbTarget = 0.0; // E^tb_{i,k}, Eq. (3)
    double bler = 0.0;      // E-bar^tb_{k,m}
    double wcttMs = 0.0;    // C_i(K) = A t_tx, Eq. (5): HARQ process and RBs held
};

// --- Eq. (2): number of TBs a packet segments into ------------------------
//
// The paper writes N = ceil(S_i / S_k). Every RLC PDU carries its own header,
// so the payload a transport block can actually carry is S_k - h; passing
// h = 0 reproduces the paper's form exactly.
//
// Returns -1 when the header leaves no payload room in this transport block.
inline int NumSegments(std::uint32_t packetBytes, std::uint32_t tbsBytes,
                       std::uint32_t headerBytes)
{
    if (tbsBytes <= headerBytes)
        return -1;
    const std::uint32_t payload = tbsBytes - headerBytes;
    if (packetBytes == 0)
        return 1;
    return static_cast<int>((packetBytes + payload - 1) / payload);
}

// --- Eq. (3): maximum admissible per-TB error rate ------------------------
//
// E^tb = 1 - (1 - E_i)^(1/N). Evaluated through log1p/expm1 because the
// direct form loses most of its significant digits at the error rates URLLC
// flows actually ask for (1e-5 and below).
inline double PerTbErrorTarget(double packetError, int numSegments)
{
    if (numSegments <= 1)
        return packetError;
    return -std::expm1(std::log1p(-packetError) / numSegments);
}

// --- Eq. (4): minimum number of transmission attempts ---------------------
//
// Smallest integer A with (E-bar^tb)^A <= E^tb. Computed from the closed form
// and then corrected in both directions, so a configuration is never accepted
// or rejected on a floating-point tie.
//
// Returns -1 when no A <= aMax satisfies the target.
//
// capAtAMax = false lifts that cap and returns the attempt count the target
// actually needs, however large. It exists for the baseline schedulers, which
// are compared without the Section V feasibility screen -- see the
// enforceLimits note on EnumerateConfigurations below.
inline int RequiredAttempts(double etbTarget, double bler, int aMax,
                            bool capAtAMax = true)
{
    // With no cap the correction loop below needs some ceiling of its own, or
    // a bler numerically indistinguishable from 1 would spin forever. No real
    // MCS row comes close to it.
    constexpr int kUncappedAttemptsBound = 1000;

    if (!(etbTarget > 0.0) || !(bler > 0.0) || bler >= 1.0 || aMax < 1)
        return -1;

    int attempts = 1;
    if (bler > etbTarget)
    {
        const double exact = std::log(etbTarget) / std::log(bler);
        attempts = static_cast<int>(std::ceil(exact));
        if (attempts < 1)
            attempts = 1;
    }

    while (attempts > 1 && std::pow(bler, attempts - 1) <= etbTarget)
        --attempts;
    const int ceiling = capAtAMax ? aMax : kUncappedAttemptsBound;
    while (attempts <= ceiling && std::pow(bler, attempts) > etbTarget)
        ++attempts;

    if (!capAtAMax)
        return attempts <= kUncappedAttemptsBound ? attempts : -1;
    return attempts <= aMax ? attempts : -1;
}

// --- Eq. (5): worst-case transmission time --------------------------------
//
// C = A * t_tx: how long a TB holds its HARQ process and its resource blocks,
// every attempt included. The upper-layer delay t_up is not part of it: it is
// spent in the other layers and holds neither, but it counts towards the
// packet's deadline. A configuration is therefore admissible only if
// C + t_up <= D (EnumerateConfigurations below), and a scheduler must end a
// TB t_up before the deadline of its packet.
inline double Wctt(int attempts, double txMs)
{
    return attempts * txMs;
}

// --- Eq. (6): Pareto dominance over (N, A, B) -----------------------------
inline bool Dominates(const Candidate &a, const Candidate &b)
{
    if (a.segments > b.segments || a.attempts > b.attempts || a.rbs > b.rbs)
        return false;
    return a.segments < b.segments || a.attempts < b.attempts || a.rbs < b.rbs;
}

// Configurations agreeing on all three of (N, A, B) are interchangeable to the
// scheduler -- it only ever consumes N, A (through C) and B -- but neither
// dominates the other, so a plain dominance sweep would keep every one of them
// and multiply the size of the candidate cross-product for nothing. Collapse
// each such class to its most robust member first.
inline std::vector<Candidate> ParetoFilter(const std::vector<Candidate> &candidates)
{
    std::vector<Candidate> unique;
    for (const auto &c : candidates)
    {
        auto same = std::find_if(unique.begin(), unique.end(), [&](const Candidate &u)
                                 { return u.segments == c.segments &&
                                          u.attempts == c.attempts &&
                                          u.rbs == c.rbs; });
        if (same == unique.end())
            unique.push_back(c);
        else if (c.bler < same->bler)
            *same = c;
    }

    std::vector<Candidate> pareto;
    for (const auto &c : unique)
    {
        const bool dominated = std::any_of(unique.begin(), unique.end(), [&](const Candidate &o)
                                           { return Dominates(o, c); });
        if (!dominated)
            pareto.push_back(c);
    }

    std::sort(pareto.begin(), pareto.end(), [](const Candidate &a, const Candidate &b)
              {
        if (a.segments != b.segments) return a.segments < b.segments;
        if (a.attempts != b.attempts) return a.attempts < b.attempts;
        if (a.rbs != b.rbs) return a.rbs < b.rbs;
        return a.m < b.m; });
    return pareto;
}

// --- Section V as a whole -------------------------------------------------
//
// Applies (2), (3), (4) and (5) to every (TB size, MCS) pair in `tables` and
// keeps those satisfying both necessary conditions: A <= aMax, and
// C + t_up <= D (upperLayerMs is t_up).
// The result is the input to ParetoFilter.
// enforceLimits = false drops the two feasibility screens of Section V: the
// Eq. (4) cap A <= A^max, and the Eq. (5) check C + t_up <= D. What comes back
// is then every (k, m) pair that segments at all, whether or not it can meet
// the flow's deadline or stay inside the HARQ attempt budget.
//
// It is how the baseline schedulers are configured. Deriving A from the
// reliability target and screening the result against the deadline is the
// contribution under evaluation, so a method being compared against it must
// not be handed the screen: the baselines select from the unscreened set and
// their misses are then their own. The planner keeps the default and the ILP
// path is unchanged.
inline std::vector<Candidate> EnumerateConfigurations(
    const FlowSpec &flow, const std::vector<TbsTable> &tables,
    std::uint32_t headerBytes, int aMax, double txMs, double upperLayerMs,
    bool enforceLimits = true)
{
    std::vector<Candidate> feasible;

    for (const auto &table : tables)
    {
        const int segments = NumSegments(flow.packetBytes,
                                         static_cast<std::uint32_t>(table.tbs),
                                         headerBytes);
        if (segments < 1)
            continue; // header leaves no payload room in this TB size

        const double etb = PerTbErrorTarget(flow.packetError, segments);

        for (const auto &row : table.mcs)
        {
            const int attempts = RequiredAttempts(etb, row.bler, aMax, enforceLimits);
            if (attempts < 1)
                continue; // Eq. (4) needs more than A^max attempts

            const double wctt = Wctt(attempts, txMs);
            if (enforceLimits && wctt + upperLayerMs > flow.deadlineMs)
                continue; // Section V-C: C + t_up > D is infeasible in isolation

            Candidate c;
            c.tbs = table.tbs;
            c.m = row.m;
            c.segments = segments;
            c.attempts = attempts;
            c.rbs = row.b;
            c.etbTarget = etb;
            c.bler = row.bler;
            c.wcttMs = wctt;
            feasible.push_back(c);
        }
    }

    return feasible;
}

// Convenience: the Pareto set P_i of Eq. (6). Empty means the flow -- and
// therefore the whole flow set -- is infeasible.
inline std::vector<Candidate> SelectConfigurations(
    const FlowSpec &flow, const std::vector<TbsTable> &tables,
    std::uint32_t headerBytes, int aMax, double txMs, double upperLayerMs,
    bool enforceLimits = true)
{
    return ParetoFilter(
        EnumerateConfigurations(flow, tables, headerBytes, aMax, txMs, upperLayerMs,
                                enforceLimits));
}

} // namespace urllc

#endif
