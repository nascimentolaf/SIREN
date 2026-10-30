#ifndef PLANNER_HPP
#define PLANNER_HPP

#include <cstdint>
#include <string>
#include <vector>

#include "urllc_config.hpp"

struct Flow
{
    std::string app;
    double error;
    double period;
    double deadline;
    uint32_t packetSize;
    std::string type;
};

// Radio and real-time parameters the planner needs to turn a flow into a set
// of transmission configurations. All times in milliseconds.
struct PlannerParams
{
    uint32_t rlcHeaderBytes = 8;
    int aMax = 4;         // A^max, the layer-wide cap on transmission attempts
    double txMs = 0.0;    // t_tx : one attempt, incl. HARQ feedback; C = A t_tx
    double upperMs = 0.0; // t_up : upper-layer processing delay; holds no HARQ
                          // process or RB, but counts towards D (C + t_up <= D)
};

// Runs Section V for every flow and writes planner_report.json.
// Returns the number of flows with an empty Pareto set (Eq. (6)), i.e. flows
// that make the whole flow set infeasible.
int GenerateReport(const std::vector<Flow> &flowset,
                   const std::vector<urllc::TbsTable> &tables,
                   const PlannerParams &params,
                   const std::string &filename);

#endif
