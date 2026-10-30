// ./planner flowset.json mcs_configurations.json planner_report.json
//
// Section V of the paper: for every flow, enumerate the transmission
// configurations K = (k, m) satisfying its reliability (Eqs. (2)-(4)) and
// timing (Eq. (5)) constraints, and keep the Pareto-optimal ones (Eq. (6)).

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <string>
#include <vector>

#include "planner.hpp"
#include "jsonLoad.hpp"

namespace
{

// std::atof(std::getenv(name)) segfaults when the variable is unset, because
// getenv returns nullptr. Running ./planner outside scripts/parameters.sh
// therefore used to crash with no diagnostic at all; fail loudly instead,
// matching how the baseline schedulers report a missing parameter.
double requireEnvDouble(const char *name)
{
    const char *raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0')
    {
        throw std::runtime_error(
            std::string("Required environment variable is missing: ") + name +
            " (source scripts/parameters.sh before running the planner)");
    }

    char *end = nullptr;
    const double value = std::strtod(raw, &end);
    if (end == raw || *end != '\0')
    {
        throw std::runtime_error(
            std::string("Environment variable ") + name +
            " is not a number: " + raw);
    }
    return value;
}

double envDoubleOptional(const char *name, double fallback)
{
    const char *raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0')
        return fallback;

    char *end = nullptr;
    const double value = std::strtod(raw, &end);
    if (end == raw || *end != '\0')
        throw std::runtime_error(
            std::string("Environment variable ") + name +
            " is not a number: " + raw);
    return value;
}

long envLongOptional(const char *name, long fallback)
{
    const char *raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0')
        return fallback;

    char *end = nullptr;
    const long value = std::strtol(raw, &end, 10);
    if (end == raw || *end != '\0' || value < 0)
        throw std::runtime_error(
            std::string("Environment variable ") + name +
            " must be a non-negative whole number, got: " + raw);
    return value;
}

} // namespace

int main(int argc, char *argv[])
{
    std::cout << "==============================\n";
    std::cout << "=== [ConfigurationPlanner] ===\n";
    std::cout << "==============================\n";

    if (argc != 4)
    {
        std::cerr
            << "Usage: " << argv[0]
            << " <flowset_file> <mcs_config_file> <output_file>\n";

        return -1;
    }

    const std::string flowFile = argv[1];
    const std::string configFile = argv[2];
    const std::string outputFile = argv[3];

    std::vector<Flow> flowset;
    std::vector<urllc::TbsTable> tables;

    if (!LoadFlowset(flowFile, flowset))
        return -1;

    std::cout << "Loaded " << flowset.size() << " applications\n";

    if (!LoadMcsTable(configFile, tables))
        return -1;

    try
    {
        // Keep only the tables measured at SINR_DB, as the baseline schedulers
        // do (parse_mcs_table), so that every method plans from the same
        // link-layer table when the file holds several SINRs.
        const double sinrDb = envDoubleOptional("SINR_DB", 10.0);
        tables.erase(std::remove_if(tables.begin(), tables.end(),
                                    [&](const urllc::TbsTable &t)
                                    { return std::abs(t.sinr - sinrDb) > 1e-9; }),
                     tables.end());
        if (tables.empty())
            throw std::runtime_error(
                "No MCS configurations found at SINR_DB=" +
                std::to_string(sinrDb) + " dB in " + configFile);

        std::cout << "Loaded " << tables.size() << " TBS tables at "
                  << sinrDb << " dB SINR\n";

        PlannerParams params;

        // t_tx of Eq. (5): one transmission attempt occupies the air interface
        // for a TTI and then waits out the HARQ feedback before the next one.
        const double ttiMs = requireEnvDouble("TTI") * 1000.0;
        const double feedbackMs = requireEnvDouble("FEEDBACK_PER_RET") * 1000.0;
        params.txMs = ttiMs + feedbackMs;
        params.upperMs = requireEnvDouble("UPPER_LAYER_DELAY") * 1000.0;
        params.rlcHeaderBytes =
            static_cast<uint32_t>(envLongOptional("RLC_HEADER_BYTES", 8));
        params.aMax = static_cast<int>(envLongOptional("A_MAX", 4));

        if (params.aMax < 1)
            throw std::runtime_error("A_MAX must be at least 1");

        std::cout << "Segmenting against TBS ";
        for (std::size_t i = 0; i < tables.size(); ++i)
            std::cout << (i ? ", " : "") << tables[i].tbs;
        std::cout << " (RLC header " << params.rlcHeaderBytes << " B, "
                  << "A_max " << params.aMax << ", "
                  << "t_tx " << params.txMs << " ms, "
                  << "t_up " << params.upperMs << " ms)\n";

        const int infeasible =
            GenerateReport(flowset, tables, params, outputFile);

        if (infeasible > 0)
        {
            std::cout
                << "Report generated: " << infeasible << " of " << flowset.size()
                << " flow(s) have an empty Pareto set; the flow set is infeasible.\n";
        }
        else
        {
            std::cout << "Report generated successfully\n";
        }
    }
    catch (const std::exception &e)
    {
        std::cerr << "[error] planner: " << e.what() << '\n';
        return 1;
    }

    return 0;
}
