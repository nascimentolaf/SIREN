#ifndef JSON_LOAD_HPP
#define JSON_LOAD_HPP

#include <fstream>
#include <iostream>

#include "json.hpp"
#include "planner.hpp"

using json = nlohmann::json;

inline bool LoadFlowset(const std::string &filename, std::vector<Flow> &flowset)
{
    std::ifstream file(filename);

    if (!file.is_open())
    {
        std::cerr << "Cannot open flowset file\n";
        return false;
    }

    json j;
    file >> j;

    for (const auto &item : j)
    {
        Flow flow;

        flow.app = item["app"];
        flow.error = item["error"];
        flow.period = item["period"];
        flow.deadline = item["deadline"];
        flow.packetSize = item["packetsize"];
        flow.type = item["type"];

        flowset.push_back(flow);
    }

    return true;
}

// Reads the link-layer table: one row per (TBS, MCS) carrying the per-attempt
// BLER and its resource-block cost. The attempt count is *not* stored -- the
// planner derives it per flow through Eq. (4), because it depends on that
// flow's Eq. (3) target.
inline bool LoadMcsTable(const std::string &filename,
                         std::vector<urllc::TbsTable> &tables)
{
    std::ifstream file(filename);

    if (!file.is_open())
    {
        std::cerr << "Cannot open MCS configuration file\n";
        return false;
    }

    json j;
    file >> j;

    for (const auto &item : j)
    {
        if (!item.contains("mcs"))
        {
            std::cerr
                << "MCS file is in the retired decade-bucket format (no 'mcs' key).\n"
                   "Regenerate it with bler/mcs_configurations/"
                   "generate_mcs_configurations.py\n";
            return false;
        }

        urllc::TbsTable table;
        table.tbs = item["tbs"];
        table.sinr = item.value("sinr", 0.0);

        for (const auto &row : item["mcs"])
        {
            urllc::McsRow mcs;
            mcs.m = row["m"];
            mcs.bler = row["bler"];
            mcs.b = row["b"];
            table.mcs.push_back(mcs);
        }

        tables.push_back(table);
    }

    return true;
}

#endif
