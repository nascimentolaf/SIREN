// Regression test for the Section V arithmetic in urllc_config.hpp.
//
// Build and run:  make -C common test
//
// The BLER/RB figures below are the link simulation's output for MCS 0-4
// (MCS 0-2 are Table II of the paper), fixed here so that this test pins the
// equations themselves and stays valid however input/mcs_configurations.json
// is regenerated.

#include "urllc_config.hpp"

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

namespace
{

int failures = 0;

void check(bool condition, const std::string &what)
{
    if (!condition)
    {
        std::printf("  FAIL  %s\n", what.c_str());
        ++failures;
    }
}

void checkClose(double got, double want, double tol, const std::string &what)
{
    if (std::fabs(got - want) > tol)
    {
        std::printf("  FAIL  %s: got %.6g, want %.6g\n", what.c_str(), got, want);
        ++failures;
    }
}

// Table II of the paper, MCS 0-4 for each of the four TB sizes.
std::vector<urllc::TbsTable> paperTableII()
{
    return {
        {66, 10.0, {{0, 0.01903, 15}, {1, 0.03742, 12}, {2, 0.05595, 9}, {3, 0.10877, 7}, {4, 0.18717, 6}}},
        {129, 10.0, {{0, 0.00673, 29}, {1, 0.01535, 22}, {2, 0.03316, 18}, {3, 0.07142, 14}, {4, 0.14373, 11}}},
        {261, 10.0, {{0, 0.00216, 58}, {1, 0.00519, 44}, {2, 0.01484, 36}, {3, 0.04373, 28}, {4, 0.12656, 23}}},
        {469, 10.0, {{0, 0.00223, 103}, {1, 0.00266, 79}, {2, 0.00637, 64}, {3, 0.02414, 50}, {4, 0.09935, 40}}},
    };
}

// Eq. (2): with no header this is exactly ceil(S_i / S_k).
void testSegmentation()
{
    std::printf("Eq. (2) segmentation\n");
    check(urllc::NumSegments(261, 261, 0) == 1, "261 B into a 261 B TB is one segment");
    check(urllc::NumSegments(261, 129, 0) == 3, "261 B into 129 B TBs is three segments");
    check(urllc::NumSegments(261, 66, 0) == 4, "261 B into 66 B TBs is four segments");

    // Each RLC PDU carries its own header, so the usable payload is S_k - h.
    check(urllc::NumSegments(261, 261, 8) == 2, "an 8 B header pushes 261 B out of one 261 B TB");
    check(urllc::NumSegments(253, 261, 8) == 1, "253 B still fits one 261 B TB with an 8 B header");
    check(urllc::NumSegments(100, 66, 66) == -1, "a header filling the TB leaves no payload room");
}

// Eq. (3): the exact target, not a decade-rounded one. Rounding to the
// nearest power of ten used to relax N = 2 and N = 3 by 2-3x.
void testPerTbTarget()
{
    std::printf("Eq. (3) per-TB reliability target\n");
    checkClose(urllc::PerTbErrorTarget(1e-5, 1), 1e-5, 1e-18, "N=1 is the packet target");
    checkClose(urllc::PerTbErrorTarget(1e-5, 2), 5.0e-6, 1e-10, "N=2 halves the budget");
    checkClose(urllc::PerTbErrorTarget(1e-5, 3), 3.333e-6, 1e-9, "N=3 thirds the budget");
    checkClose(urllc::PerTbErrorTarget(1e-5, 4), 2.5e-6, 1e-10, "N=4 quarters the budget");

    // The property the equation exists to guarantee.
    for (int n = 1; n <= 12; ++n)
    {
        const double etb = urllc::PerTbErrorTarget(1e-5, n);
        check(1.0 - std::pow(1.0 - etb, n) <= 1e-5 * (1.0 + 1e-9),
              "N=" + std::to_string(n) + " target composes back to E_i");
    }
}

// Eq. (4): smallest A with bler^A <= target, capped at A^max.
void testAttempts()
{
    std::printf("Eq. (4) transmission attempts\n");
    const double etb3 = urllc::PerTbErrorTarget(1e-5, 3); // 3.333e-6
    check(urllc::RequiredAttempts(etb3, 0.00673, 4) == 3, "the paper's worked (129, MCS0) case gives A=3");
    check(urllc::RequiredAttempts(1e-5, 0.00216, 4) == 2, "(261, MCS0) at N=1 gives A=2");
    check(urllc::RequiredAttempts(1e-5, 0.04373, 4) == 4, "(261, MCS3) at N=1 gives A=4");
    check(urllc::RequiredAttempts(1e-5, 0.12656, 4) == -1, "(261, MCS4) needs more than A^max");
    check(urllc::RequiredAttempts(1e-5, 1e-5, 4) == 1, "a row already at the target needs one attempt");
    check(urllc::RequiredAttempts(1e-5, 1e-4, 2) == 2, "exact powers do not round the wrong way");

    // Whatever it returns must actually reach the target.
    for (const auto &t : paperTableII())
        for (const auto &row : t.mcs)
        {
            const int a = urllc::RequiredAttempts(1e-5, row.bler, 8);
            if (a > 0)
            {
                check(std::pow(row.bler, a) <= 1e-5, "A reaches the target");
                check(a == 1 || std::pow(row.bler, a - 1) > 1e-5, "A is minimal");
            }
        }
}

// Eq. (6) on the paper's worked example: F2, S = 261 B, E = 1e-5.
void testParetoExample()
{
    std::printf("Eq. (6) Pareto set for the paper's F2 example\n");

    const urllc::FlowSpec f2{261, 1e-5, 1000.0}; // deadline slack: isolate Section V-D
    const auto pareto = urllc::SelectConfigurations(f2, paperTableII(), 0, 4, 1.0, 0.0);

    // (N, A, B) triples, in the order ParetoFilter sorts them.
    const std::vector<std::vector<int>> want = {
        {1, 2, 58},  // (261, MCS0)
        {1, 3, 36},  // (261, MCS2)
        {1, 4, 28},  // (261, MCS3)
        {3, 3, 29},  // (129, MCS0)
        {3, 4, 18},  // (129, MCS2)
        {4, 4, 12},  // (66,  MCS1)
    };

    check(pareto.size() == want.size(),
          "P_2 has " + std::to_string(want.size()) + " configurations, got " +
              std::to_string(pareto.size()));

    for (std::size_t i = 0; i < pareto.size() && i < want.size(); ++i)
    {
        const auto &c = pareto[i];
        const std::string tag = "P_2[" + std::to_string(i) + "]";
        check(c.segments == want[i][0] && c.attempts == want[i][1] && c.rbs == want[i][2],
              tag + " = (N=" + std::to_string(c.segments) + ",A=" + std::to_string(c.attempts) +
                  ",B=" + std::to_string(c.rbs) + "), want (N=" + std::to_string(want[i][0]) +
                  ",A=" + std::to_string(want[i][1]) + ",B=" + std::to_string(want[i][2]) + ")");
    }

    // Nothing in a Pareto set may dominate anything else in it.
    for (const auto &a : pareto)
        for (const auto &b : pareto)
            check(&a == &b || !urllc::Dominates(a, b), "no member of P_2 dominates another");

    // Configurations the paper's Table III lists but Eq. (6) excludes.
    for (const auto &c : pareto)
    {
        check(!(c.segments == 4 && c.rbs == 15), "(66, MCS0) is dominated by (66, MCS1)");
        check(!(c.segments == 1 && c.rbs == 44), "(261, MCS1) is dominated by (261, MCS2)");
    }
}

// Section V-C: C + t_up > D removes a configuration, and C itself is the HARQ
// occupation A * t_tx, without t_up.
void testDeadlineFilter()
{
    std::printf("Eq. (5) deadline filter\n");
    const auto tables = paperTableII();

    // t_tx = 1 ms, t_up = 4 ms: C = A, admissible while A + 4 <= D.
    const urllc::FlowSpec tight{261, 1e-5, 6.0}; // admits A <= 2 only
    const auto kept = urllc::SelectConfigurations(tight, tables, 0, 4, 1.0, 4.0);
    check(!kept.empty(), "a 6 ms deadline keeps the two-attempt configurations");
    for (const auto &c : kept)
    {
        checkClose(c.wcttMs, c.attempts * 1.0, 1e-12, "C is A * t_tx, without t_up");
        check(c.wcttMs + 4.0 <= 6.0, "a kept configuration meets the deadline once t_up is added");
        check(c.attempts <= 2, "a 6 ms deadline admits at most two attempts");
    }

    const urllc::FlowSpec impossible{261, 1e-5, 4.0}; // t_up alone fills the deadline
    check(urllc::SelectConfigurations(impossible, tables, 0, 4, 1.0, 4.0).empty(),
          "a deadline t_up alone exhausts leaves P_i empty");
}

} // namespace

int main()
{
    testSegmentation();
    testPerTbTarget();
    testAttempts();
    testParetoExample();
    testDeadlineFilter();

    if (failures == 0)
        std::printf("\nAll Section V checks passed.\n");
    else
        std::printf("\n%d check(s) FAILED.\n", failures);
    return failures == 0 ? 0 : 1;
}
