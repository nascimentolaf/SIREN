#ifndef FLOWSET_HPP
#define FLOWSET_HPP
#include <string>
#include <cmath>
#include <vector>
#include <map>
#include <fstream>
#include <algorithm>
#include <random>
#include <filesystem>
#include <numeric>
#include <iostream>

#include "json.hpp"

using json = nlohmann::json;



using json = nlohmann::json;
namespace fs = std::filesystem;


struct AppCombination
{
    std::vector<int> indices;
};

void generateCombinations(int start, int depth, int k, int n, std::vector<int> &current, std::vector<AppCombination> &combinations);
bool writeFlowSet(const std::string &directory, int setNumber, const json &apps, const std::vector<std::string> &appNames, const AppCombination &combination);


#endif