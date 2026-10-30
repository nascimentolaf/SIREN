#include "applicationset.hpp"

void generateCombinations(int start, int depth, int k, int n, std::vector<int> &current, std::vector<AppCombination> &combinations)
{
    if (depth == k)
    {
        combinations.push_back({current});
        return;
    }

    for (int i = start; i <= n - (k - depth); ++i)
    {
        current[depth] = i;

        generateCombinations(
            i + 1,
            depth + 1,
            k,
            n,
            current,
            combinations);
    }
}

bool writeFlowSet(const std::string &directory, int setNumber, const json &apps, const std::vector<std::string> &appNames, const AppCombination &combination)
{
    json output = json::array();

    for (int index : combination.indices)
    {
        const std::string& appName = appNames[index];

        json app = apps.at(appName);

        // Store the application name as a field
        app["app"] = appName;

        // Use "packetsize" instead of "packet_size" if needed
        if (app.contains("packet_size"))
        {
            app["packetsize"] = app["packet_size"];
            app.erase("packet_size");
        }

        output.push_back(app);
    }

    char filename[64];

    std::snprintf(
        filename,
        sizeof(filename),
        "flowset_%04d.json",
        setNumber);

    fs::path filepath = fs::path(directory) / filename;

    std::ofstream file(filepath);

    if (!file.is_open())
    {
        std::cerr
            << "Error: Could not create "
            << filepath
            << std::endl;

        return false;
    }

    file << output.dump(4) << '\n';

    file.close();

    return true;
}