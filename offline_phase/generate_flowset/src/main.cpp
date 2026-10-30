#include <cstdlib>
/*
    Usage:

        ./generate_flowset <apps.json> <apps_per_set> <number_of_sets> [output_dir]

    Example:

        ./generate_flowset apps.json 4 100 /tmp/flowsets

    This means:

        - Read apps.json
        - Select 4 apps per set
        - Write 100 distinct sets as flowset_0001.json ... flowset_0100.json
          into /tmp/flowsets (created if missing)

    output_dir defaults to ../../output/flowsets, relative to the current
    directory: the repository's output/flowsets when run from this
    directory. Pass output_dir to write anywhere else.
*/

#include "applicationset.hpp"

int main(int argc, char *argv[])
{
    std::cout << "==============================\n";
    std::cout << "====  [FlowsetGenerator]  ====\n";
    std::cout << "==============================\n";


    if (argc != 4 && argc != 5)
    {
        std::cerr
            << "Usage: "
            << argv[0]
            << " <apps.json> <apps_per_set> <number_of_sets> [output_dir]"
            << std::endl;

        std::cerr
            << "Example: "
            << argv[0]
            << " apps.json 4 100 /tmp/flowsets"
            << std::endl;

        return 1;
    }

    /*
        Parse command-line arguments.
    */

    const std::string inputFile = argv[1];

    int appsPerSet = std::stoi(argv[2]);

    long requestedSets = std::stol(argv[3]);

    if (appsPerSet <= 0)
    {
        std::cerr
            << "Error: apps_per_set must be greater than 0."
            << std::endl;

        return 1;
    }

    if (requestedSets <= 0)
    {
        std::cerr
            << "Error: number_of_sets must be greater than 0."
            << std::endl;

        return 1;
    }

    /*
        Read JSON database.
    */

    std::ifstream file(inputFile);

    if (!file.is_open())
    {
        std::cerr
            << "Error: Could not open "
            << inputFile
            << std::endl;

        return 1;
    }

    json apps;

    try
    {
        file >> apps;
    }
    catch (const json::parse_error &e)
    {
        std::cerr
            << "JSON parsing error: "
            << e.what()
            << std::endl;

        return 1;
    }

    file.close();

    /*
        Check that the JSON root is an object.
    */

    if (!apps.is_object())
    {
        std::cerr
            << "Error: apps.json must contain a JSON object."
            << std::endl;

        return 1;
    }

    /*
        Extract application names.
    */

    std::vector<std::string> appNames;

    for (const auto &[name, data] : apps.items())
    {
        appNames.push_back(name);
    }

    const int appCount =
        static_cast<int>(appNames.size());

    /*
        Validate requested number of apps.
    */

    if (appsPerSet > appCount)
    {
        std::cerr
            << "Error: Cannot select "
            << appsPerSet
            << " apps from "
            << appCount
            << " available apps."
            << std::endl;

        return 1;
    }

    /*
        Generate all possible combinations.
    */

    std::vector<AppCombination> combinations;

    std::vector<int> current(appsPerSet);

    generateCombinations(
        0,
        0,
        appsPerSet,
        appCount,
        current,
        combinations);

    const long totalCombinations =
        static_cast<long>(combinations.size());

    /*
        Print information.
    */

    std::cout
        << "Apps available:              "
        << appCount
        << '\n';

    std::cout
        << "Apps per set:                "
        << appsPerSet
        << '\n';

    std::cout
        << "Unique possible combinations: "
        << totalCombinations
        << '\n';

    std::cout
        << "Sets requested:              "
        << requestedSets
        << '\n';


    /*
        Drawing without repetition cannot produce more sets than there are
        combinations. Capping silently used to be possible, which left the
        evaluation running on a smaller sample than intended; refuse instead,
        so the catalogue has to be large enough for the requested number of
        flow sets (C(appCount, appsPerSet) >= requestedSets).
    */
    if (requestedSets > totalCombinations)
    {
        std::cerr
            << "Error: "
            << requestedSets
            << " sets requested but only "
            << totalCombinations
            << " distinct combinations of "
            << appsPerSet
            << " out of "
            << appCount
            << " applications exist. Enlarge the catalogue or ask for fewer sets."
            << std::endl;

        return 1;
    }

    long setsToGenerate = requestedSets;

    std::cout
        << "Sets to generate:            "
        << setsToGenerate
        << '\n';

    // Seeded so the sample is reproducible: FLOWSET_SEED in the environment
    // (scripts/parameters.sh exports it); unset or empty falls back to a
    // random_device draw, the pre-existing behaviour.
    std::mt19937 generator;
    const char *seedEnv = std::getenv("FLOWSET_SEED");
    if (seedEnv && *seedEnv)
    {
        generator.seed(static_cast<std::mt19937::result_type>(std::stoul(seedEnv)));
        std::cout << "Seed:                        " << seedEnv << '\n';
    }
    else
    {
        std::random_device rd;
        generator.seed(rd());
        std::cout << "Seed:                        random_device (set FLOWSET_SEED to reproduce)\n";
    }

    std::shuffle(
        combinations.begin(),
        combinations.end(),
        generator);

    const std::string outputDirectory =
        (argc == 5) ? argv[4] : "../../output/flowsets";

    try
    {
        fs::create_directories(outputDirectory);
    }
    catch (const fs::filesystem_error &e)
    {
        std::cerr
            << "Error creating output directory: "
            << e.what()
            << std::endl;

        return 1;
    }

    /*
        Write selected combinations.
    */

    long generated = 0;

    for (long i = 0; i < setsToGenerate; ++i)
    {
        if (writeFlowSet(
                outputDirectory,
                static_cast<int>(i + 1),
                apps,
                appNames,
                combinations[i]))
        {
            ++generated;
        }
    }

    const std::string where = fs::absolute(outputDirectory).lexically_normal().string();

    // A file that could not be written leaves a gap in the batch; the caller
    // would go on with fewer flowsets than it asked for, so fail instead.
    if (generated != setsToGenerate)
    {
        std::cerr
            << "Error: wrote " << generated << " of " << setsToGenerate
            << " flowsets into " << where << std::endl;

        return 1;
    }

    std::cout << "\nSuccessfully generated " << generated << " JSON files in " << where << std::endl;

    return 0;
}