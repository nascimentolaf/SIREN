#include "scheduler.hpp"

[[noreturn]] void die(const std::string &message)
{
    throw std::runtime_error(message);
}

int getenv_int_required(const char *name)
{
    const char *raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0')
        die(std::string("Required environment variable is missing: ") + name);
    char *end = nullptr;
    const long value = std::strtol(raw, &end, 10);
    if (end == raw || *end != '\0' || value < std::numeric_limits<int>::min() || value > std::numeric_limits<int>::max())
    {
        die(std::string("Invalid integer environment variable ") + name + ": " + raw);
    }
    return static_cast<int>(value);
}

std::int64_t scaled_period(double period_ms)
{
    const double scaled = period_ms * kPeriodScale;
    if (!std::isfinite(scaled) || scaled <= 0.0 || scaled > static_cast<double>(std::numeric_limits<std::int64_t>::max()))
    {
        die("Period out of supported range: " + std::to_string(period_ms) + " ms");
    }
    const auto value = static_cast<std::int64_t>(std::llround(scaled));
    if (value <= 0)
        die("Period rounds to zero at microsecond resolution: " + std::to_string(period_ms) + " ms");
    return value;
}

std::int64_t safe_lcm(std::int64_t a, std::int64_t b)
{
    const std::int64_t g = std::gcd(a, b);
    const std::int64_t q = a / g;
    if (q > std::numeric_limits<std::int64_t>::max() / b)
        die("Hyperperiod overflows 64-bit integer range");
    return q * b;
}

double compute_hyperperiod_ms(const std::vector<Flow> &flows)
{
    if (flows.empty())
        die("Cannot compute hyperperiod for an empty flowset");
    std::int64_t h = scaled_period(flows.front().period_ms);
    for (std::size_t i = 1; i < flows.size(); ++i)
        h = safe_lcm(h, scaled_period(flows[i].period_ms));
    return static_cast<double>(h) / kPeriodScale;
}

double getenv_double_optional(const char *name, double fallback)
{
    const char *raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0')
        return fallback;
    char *end = nullptr;
    const double value = std::strtod(raw, &end);
    if (end == raw || *end != '\0' || !std::isfinite(value))
    {
        die(std::string("Invalid floating-point environment variable ") + name + ": " + raw);
    }
    return value;
}

std::string lower_copy(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c)
                   { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::string require_string(const json &obj, const char *key, const std::string &where)
{
    if (!obj.contains(key) || !obj.at(key).is_string())
        die(where + ": missing/invalid string field '" + key + "'");
    return obj.at(key).get<std::string>();
}

std::vector<Flow> parse_flowset(const std::string &path)
{
    std::ifstream input(path);
    if (!input)
        die("Cannot open flowset: " + path);
    json root;
    try
    {
        input >> root;
    }
    catch (const std::exception &e)
    {
        die("Invalid flowset JSON '" + path + "': " + e.what());
    }
    if (!root.is_array())
        die("Flowset must be a JSON array: " + path);

    std::vector<Flow> flows;
    flows.reserve(root.size());
    for (std::size_t i = 0; i < root.size(); ++i)
    {
        const auto &item = root.at(i);
        const std::string where = "flowset[" + std::to_string(i) + "]";
        if (!item.is_object())
            die(where + ": must be an object");
        Flow f;
        f.app = require_string(item, "app", where);
        f.deadline_ms = require_number(item, "deadline", where);
        f.error = require_number(item, "error", where);
        f.packet_size_bytes = require_nonnegative_size(item, "packetsize", where);
        f.period_ms = require_number(item, "period", where);
        f.type = require_string(item, "type", where);
        if (f.app.empty())
            die(where + ": app cannot be empty");
        if (!(f.deadline_ms >= 0.0) || !std::isfinite(f.deadline_ms))
            die(where + ": deadline must be finite and non-negative");
        if (!(f.error > 0.0 && f.error <= 1.0) || !std::isfinite(f.error))
            die(where + ": error must be in (0,1]");
        if (!(f.period_ms > 0.0) || !std::isfinite(f.period_ms))
            die(where + ": period must be finite and > 0");
        if (lower_copy(f.type) != "rt")
            die(where + ": unsupported flow type '" + f.type + "' (only RT is supported)");
        flows.push_back(std::move(f));
    }
    if (flows.empty())
        die("Flowset is empty: " + path);

    std::unordered_map<std::string, bool> seen;
    for (const auto &f : flows)
    {
        if (!seen.emplace(f.app, true).second)
            die("Duplicate app identifier in flowset: " + f.app);
    }
    return flows;
}

std::vector<FlowSpec> parse_flowset(const json &j)
{
    std::vector<FlowSpec> flows;
    for (const auto &item : j)
    {
        FlowSpec f;
        f.app = item.at("app").get<std::string>();
        f.deadline = item.at("deadline").get<double>();
        f.error = item.at("error").get<double>();
        f.packetsize = item.at("packetsize").get<double>();
        f.period = item.at("period").get<double>();
        f.type = item.value("type", std::string("RT"));
        // Optional: the QoS-PF scheduler uses the declared 5QI directly
        // rather than guessing one from (deadline, error). Absent for older
        // flowsets, which then fall back to the nearest-match heuristic.
        f.fiveQI = item.value("5qi", -1);
        flows.push_back(f);
    }
    return flows;
}

std::vector<FlowScheduleConfig> build_flow_configs(
    const std::vector<Flow> &flows, const RuntimeConfig &runtime,
    const std::vector<urllc::TbsTable> &tables)
{
    std::vector<FlowScheduleConfig> result;
    result.reserve(flows.size());

    for (const auto &flow : flows)
    {
        const auto c = select_flow_config(
            static_cast<std::uint32_t>(flow.packet_size_bytes), flow.error,
            static_cast<double>(flow.deadline_ms), flow.app, tables, runtime);

        // One flow carries one configuration K for every packet in the
        // hyperperiod (Section IV-A), so all its segments share a TB size.
        result.push_back(FlowScheduleConfig{
            c.tbs, c.m, c.rbs, c.attempts, c.bler, c.wcttMs, c.segments,
            std::vector<int>(static_cast<std::size_t>(c.segments), c.tbs)});
    }
    return result;
}

json make_report(
    int combo_index, const RuntimeConfig &runtime, const std::vector<Flow> &flows,
    const std::vector<FlowScheduleConfig> &flow_cfgs, const std::vector<DispatchEntry> &dispatch,
    double hyperperiod_ms, const std::string &objective)
{
    json report;
    report["combo_index"] = combo_index;
    report["config"] = {
        {"m_harq", runtime.harqs},
        {"n_rb", static_cast<int>(runtime.rbs)},
        {"tti_ms", runtime.tti_s * 1000.0},
        {"t_up_ms", upper_layer_delay_ms(runtime)},
        {"t_up_tti", upper_layer_delay_tti(runtime)},
        {"exec_time_mode", runtime.exec_time_mode},
        {"exec_sample_seed", runtime.exec_sample_seed},
        {"objective", objective}};

    // Every method reports on the same discrete grid as the ILP -- one TTI,
    // the model's time unit (Table I) -- so the online phase can consume any
    // method's dispatch table through one code path. The _ms fields are kept
    // alongside for display.
    const double tti_ms = runtime.tti_s * 1000.0;
    // Reports carry both ms and TTI fields; a value that is not a whole
    // number of TTIs would be silently rounded here, so refuse it.
    const auto to_tti = [tti_ms](double ms)
    {
        const double ticks = ms / tti_ms;
        if (std::abs(ticks - std::llround(ticks)) > 1e-6)
            die("time " + std::to_string(ms) + " ms is not a whole number of " +
                std::to_string(tti_ms) + " ms TTIs");
        return static_cast<long long>(std::llround(ticks));
    };

    report["dispatch_table"] = json::array();
    for (const auto &d : dispatch)
    {
        report["dispatch_table"].push_back({{"app", d.app},
                                            {"b", d.b},
                                            {"deadline_tti", to_tti(d.deadline_ms)},
                                            {"end_tti", to_tti(d.end_ms)},
                                            {"release_tti", to_tti(d.release_ms)},
                                            {"start_tti", to_tti(d.start_ms)},
                                            {"deadline_ms", d.deadline_ms},
                                            {"end_ms", d.end_ms},
                                            {"machine", d.machine},
                                            {"release_ms", d.release_ms},
                                            {"start_ms", d.start_ms},
                                            {"subtask", d.subtask}});
    }

    report["flows"] = json::array();
    for (std::size_t i = 0; i < flows.size(); ++i)
    {
        const auto &f = flows[i];
        const auto &c = flow_cfgs[i];
        report["flows"].push_back({{"app", f.app},
                                   {"a", c.k}, // A_i(K), Eq. (4)
                                   {"b", c.b},
                                   {"c", c.expected_c_ms}, // C_i(K), Eq. (5)
                                   {"c_tti", to_tti(c.expected_c_ms)},
                                   {"deadline", f.deadline_ms},
                                   {"deadline_tti", to_tti(f.deadline_ms)},
                                   {"m", c.m},
                                   {"num_segments", c.num_segments},
                                   {"period", f.period_ms},
                                   {"period_tti", to_tti(f.period_ms)},
                                   {"tbs", c.tbs}});
    }

    std::unordered_map<int, bool> used_machine;
    for (const auto &d : dispatch)
        used_machine[d.machine] = true;
    report["hyperperiod_ms"] = hyperperiod_ms;
    report["hyperperiod_tti"] = to_tti(hyperperiod_ms);
    report["harq_used"] = static_cast<int>(used_machine.size());
    return report;
}

// Parses the link-layer table: one row per (TBS, MCS) carrying the
// per-attempt BLER and its resource-block cost. Attempt counts are *not*
// stored -- they depend on the flow's Eq. (3) target, so each flow derives
// its own through Eq. (4).
std::vector<urllc::TbsTable> parse_mcs_table(const json &j)
{
    if (!j.is_array())
        die("MCS configuration must be a JSON array");

    std::vector<urllc::TbsTable> tables;
    for (std::size_t i = 0; i < j.size(); ++i)
    {
        const auto &row = j.at(i);
        const std::string where = "mcs[" + std::to_string(i) + "]";
        if (!row.is_object())
            die(where + ": must be an object");
        if (!row.contains("mcs"))
        {
            die("MCS file is in the retired decade-bucket format (no 'mcs' key). "
                "Regenerate it with bler/mcs_configurations/generate_mcs_configurations.py");
        }

        const double tbs_d = require_number(row, "tbs", where);
        if (tbs_d <= 0.0 || std::floor(tbs_d) != tbs_d)
            die(where + ": tbs must be a positive integer");

        urllc::TbsTable table;
        table.tbs = static_cast<int>(tbs_d);
        table.sinr = require_number(row, "sinr", where);

        const auto &rows = row.at("mcs");
        if (!rows.is_array() || rows.empty())
            die(where + ": 'mcs' must be a non-empty array");

        for (std::size_t k = 0; k < rows.size(); ++k)
        {
            const auto &entry = rows.at(k);
            const std::string entry_where = where + ".mcs[" + std::to_string(k) + "]";
            if (!entry.is_object())
                die(entry_where + ": must be an object");

            const double m = require_number(entry, "m", entry_where);
            const double bler = require_number(entry, "bler", entry_where);
            const double b = require_number(entry, "b", entry_where);
            if (m < 0 || std::floor(m) != m || b <= 0 || std::floor(b) != b)
                die(entry_where + ": m/b must be integer-valued, with b > 0");
            if (!(bler > 0.0 && bler < 1.0))
                die(entry_where + ": bler must be in (0,1)");

            urllc::McsRow mcs;
            mcs.m = static_cast<int>(m);
            mcs.bler = bler;
            mcs.b = static_cast<int>(b);
            table.mcs.push_back(mcs);
        }

        tables.push_back(std::move(table));
    }

    std::sort(tables.begin(), tables.end(),
              [](const urllc::TbsTable &a, const urllc::TbsTable &b)
              { return a.tbs < b.tbs; });
    return tables;
}

double getenv_double_required(const char *name)
{
    const char *raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0')
        die(std::string("Required environment variable is missing: ") + name);
    char *end = nullptr;
    const double value = std::strtod(raw, &end);
    if (end == raw || *end != '\0' || !std::isfinite(value))
    {
        die(std::string("Invalid floating-point environment variable ") + name + ": " + raw);
    }
    return value;
}

RuntimeConfig read_runtime_config(bool allow_auto_harqs)
{
    RuntimeConfig cfg;
    cfg.tti_s = getenv_double_required("TTI");
    cfg.rbs = static_cast<double>(getenv_int_required("RBs"));
    const char *harqs = std::getenv("HARQs");
    if (harqs != nullptr && std::string(harqs) == "auto")
    {
        if (!allow_auto_harqs)
            die("HARQs=auto is not supported by this scheduler: give a number of HARQ processes");
        cfg.harqs_auto = true;
    }
    else
    {
        cfg.harqs = getenv_int_required("HARQs");
    }
    cfg.upper_layer_delay_s = getenv_double_required("UPPER_LAYER_DELAY");

    if (!(cfg.tti_s > 0.0) || cfg.rbs <= 0.0 || (!cfg.harqs_auto && cfg.harqs <= 0) ||
        cfg.upper_layer_delay_s < 0.0)
    {
        die("TTI, RBs, HARQs must be > 0 and UPPER_LAYER_DELAY must be >= 0");
    }

    // feedback_per_ret was not specified as a required environment variable. The reference
    // timing implies 0.75 ms at TTI=0.25 ms, i.e. 3 TTIs, so use that explicit derived value
    // unless FEEDBACK_PER_RET is supplied by the experiment environment.
    cfg.feedback_per_ret_s = getenv_double_optional("FEEDBACK_PER_RET", 3.0 * cfg.tti_s);
    if (cfg.feedback_per_ret_s < 0.0)
        die("FEEDBACK_PER_RET must be >= 0");
    cfg.sinr_db = getenv_double_optional("SINR_DB", 10.0);
    cfg.rlc_header_bytes = rlc_header_bytes();

    // A^max (Section IV-B). Read from the same variable the offline planner
    // uses, so a sweep cannot give the baselines a different attempt budget.
    const double a_max = getenv_double_optional("A_MAX", 4.0);
    if (!(a_max >= 1.0) || a_max != std::floor(a_max))
        die("A_MAX must be a whole number >= 1");
    cfg.a_max = static_cast<int>(a_max);

    // Execution-time model. See RuntimeConfig for why "sampled" exists.
    if (const char *mode = std::getenv("EXEC_TIME_MODE"))
    {
        cfg.exec_time_mode = lower_copy(mode);
        if (cfg.exec_time_mode != "wcet" && cfg.exec_time_mode != "sampled")
            die("EXEC_TIME_MODE must be 'wcet' or 'sampled', got: " + cfg.exec_time_mode);
    }
    const double seed = getenv_double_optional("EXEC_SAMPLE_SEED", 42.0);
    if (seed < 0.0 || seed != std::floor(seed))
        die("EXEC_SAMPLE_SEED must be a non-negative whole number");
    cfg.exec_sample_seed = static_cast<std::uint64_t>(seed);

    // Proportional-fair averaging filter. PF_ALPHA is 1/t_c in the survey's
    // R_i(t) = (1 - 1/t_c) R_i(t-1) + (1/t_c) r_i(t): the weight of the newest
    // per-TTI sample, so 1/PF_ALPHA is the filter's memory in TTIs. Both of
    // these used to sit at hardcoded struct defaults with no way to set them,
    // even though PF_ALPHA decides how quickly an unserved flow's average
    // decays and therefore how fair the scheduler actually is.
    cfg.pf_alpha = getenv_double_optional("PF_ALPHA", cfg.pf_alpha);
    if (!(cfg.pf_alpha > 0.0 && cfg.pf_alpha <= 1.0))
        die("PF_ALPHA must be in (0,1]");

    cfg.pf_initial_throughput_bytes_per_ms =
        getenv_double_optional("PF_INITIAL_THROUGHPUT", cfg.pf_initial_throughput_bytes_per_ms);
    if (!(cfg.pf_initial_throughput_bytes_per_ms > 0.0))
        die("PF_INITIAL_THROUGHPUT must be > 0");

    return cfg;
}
std::string default_output_path(const std::string &flowset_path, const std::string &scheduler)
{
    const std::string stem = std::filesystem::path(flowset_path).stem().string();
    const std::string filename = stem + "_" + scheduler + ".json";
    return (std::filesystem::current_path() / filename).string();
}

std::vector<urllc::TbsTable> parse_mcs_table(const std::string &path, double requested_sinr_db)
{
    std::ifstream input(path);
    if (!input)
        die("Cannot open MCS configuration: " + path);
    json root;
    try
    {
        input >> root;
    }
    catch (const std::exception &e)
    {
        die("Invalid MCS JSON '" + path + "': " + e.what());
    }

    std::vector<urllc::TbsTable> tables = parse_mcs_table(root);
    tables.erase(std::remove_if(tables.begin(), tables.end(),
                                [&](const urllc::TbsTable &t)
                                { return std::abs(t.sinr - requested_sinr_db) > 1e-9; }),
                 tables.end());

    if (tables.empty())
        die("No MCS configurations found at requested SINR=" + std::to_string(requested_sinr_db) + " dB");
    return tables;
}

double require_number(const json &obj, const char *key, const std::string &where)
{
    if (!obj.contains(key) || !obj.at(key).is_number())
        die(where + ": missing/invalid numeric field '" + key + "'");
    return obj.at(key).get<double>();
}

std::size_t require_nonnegative_size(const json &obj, const char *key, const std::string &where)
{
    const double x = require_number(obj, key, where);
    if (x < 0.0 || std::floor(x) != x)
        die(where + ": field '" + key + "' must be a non-negative integer");
    return static_cast<std::size_t>(x);
}

std::size_t rlc_header_bytes()
{
    const double raw = getenv_double_optional("RLC_HEADER_BYTES", 8.0);
    if (!(raw >= 0.0) || raw != std::floor(raw))
        die("RLC_HEADER_BYTES must be a non-negative whole number of bytes");
    return static_cast<std::size_t>(raw);
}

double tb_duration_ms(int k, const RuntimeConfig &cfg)
{
    const double attempt_ms = (cfg.feedback_per_ret_s + cfg.tti_s) * 1000.0;
    return static_cast<double>(std::max(1, k)) * attempt_ms;
}

double upper_layer_delay_ms(const RuntimeConfig &cfg)
{
    return cfg.upper_layer_delay_s * 1000.0;
}

long long upper_layer_delay_tti(const RuntimeConfig &cfg)
{
    const double tti_ms = cfg.tti_s * 1000.0;
    return static_cast<long long>(std::ceil(upper_layer_delay_ms(cfg) / tti_ms - 1e-9));
}

// $FLOW_CONFIG: run this method on a configuration it did not choose.
//
// Section V normally has each method derive its own configuration K per flow
// (the Pareto front, tie-broken below). That makes one run per flow set, which
// is the right unit for comparing methods as they are meant to be used, but it
// cannot be compared against the ILP per *configuration*: the ILP sweeps the
// whole candidate set the planner emits and decides each one, while a baseline
// only ever visits its own single pick.
//
// Pointing FLOW_CONFIG at a JSON object of
//
//   {"<app>": {"tbs":..,"m":..,"segments":..,"attempts":..,
//              "rbs":..,"etb_target":..,"bler":..,"wctt_ms":..}, ...}
//
// imposes one candidate instead, so the method schedules a configuration
// chosen for it and the two are asking the same question of the same input.
// The fields are exactly urllc::Candidate, which is what the planner writes
// into planner_report.json -- both sides share common/urllc_config.hpp, so a
// candidate can be handed over verbatim rather than re-derived.
//
// Unset (the normal case), nothing here changes: every method picks its own.
namespace
{

    const json *imposed_flow_configs()
    {
        // Read once: the file is the same for every flow in the run.
        static const bool have = std::getenv("FLOW_CONFIG") != nullptr &&
                                 *std::getenv("FLOW_CONFIG") != '\0';
        static json parsed;
        static bool loaded = false;

        if (!have)
            return nullptr;

        if (!loaded)
        {
            const std::string path = std::getenv("FLOW_CONFIG");
            std::ifstream in(path);
            if (!in)
                die("Cannot open FLOW_CONFIG file: " + path);
            try
            {
                in >> parsed;
            }
            catch (const std::exception &e)
            {
                die("Invalid JSON in FLOW_CONFIG file " + path + ": " + e.what());
            }
            if (!parsed.is_object())
                die("FLOW_CONFIG must be a JSON object keyed by application name: " + path);
            loaded = true;
        }
        return &parsed;
    }

    double require_field(const json &o, const char *key, const std::string &app)
    {
        if (!o.contains(key) || !o.at(key).is_number())
            die(std::string("FLOW_CONFIG entry for ") + app + " is missing a numeric '" + key + "'");
        return o.at(key).get<double>();
    }

} // namespace

// Section V, shared by every method -- see the header for why the tie-break
// rule is fixed here rather than per scheduler.
urllc::Candidate select_flow_config(
    std::uint32_t packet_bytes, double packet_error, double deadline_ms,
    const std::string &app, const std::vector<urllc::TbsTable> &tables,
    const RuntimeConfig &runtime)
{
    if (const json *imposed = imposed_flow_configs())
    {
        // A missing app is a broken experiment, not a reason to fall back on
        // the method's own pick: that would silently mix two different units
        // into one count.
        if (!imposed->contains(app))
            die("FLOW_CONFIG has no entry for app " + app);
        const json &e = imposed->at(app);

        urllc::Candidate c;
        c.tbs = static_cast<int>(require_field(e, "tbs", app));
        c.m = static_cast<int>(require_field(e, "m", app));
        c.segments = static_cast<int>(require_field(e, "segments", app));
        c.attempts = static_cast<int>(require_field(e, "attempts", app));
        c.rbs = static_cast<int>(require_field(e, "rbs", app));
        c.etbTarget = require_field(e, "etb_target", app);
        c.bler = require_field(e, "bler", app);
        c.wcttMs = require_field(e, "wctt_ms", app);

        // The same capacity check the selection path applies: a configuration
        // wider than the cell cannot be transmitted by any policy.
        if (c.rbs > static_cast<int>(runtime.rbs))
        {
            throw InfeasibleFlowSet(
                app, "imposed configuration for app " + app + " needs " +
                         std::to_string(c.rbs) + " resource blocks, cell has " +
                         std::to_string(static_cast<int>(runtime.rbs)));
        }
        return c;
    }

    const double tx_ms = (runtime.feedback_per_ret_s + runtime.tti_s) * 1000.0;
    const double upper_ms = runtime.upper_layer_delay_s * 1000.0;

    const urllc::FlowSpec spec{packet_bytes, packet_error, deadline_ms};

    // enforceLimits = false: the baselines are not screened by Section V. The
    // Eq. (5) check C + t_up <= D is part of the contribution being
    // evaluated, so a method compared against it does not get to use it -- it
    // picks as it would without the analysis, and answers for the result.
    // A^max is different: it is a limit of the MAC layer itself (Section
    // IV-B), so the selection below keeps to it whenever some configuration
    // within it fits the cell. The ILP path keeps both screens.
    auto pareto = urllc::SelectConfigurations(
        spec, tables, static_cast<std::uint32_t>(runtime.rlc_header_bytes),
        runtime.a_max, tx_ms, upper_ms, /*enforceLimits=*/false);

    // A configuration needing more resource blocks than the cell has can never
    // be transmitted, whatever the scheduling policy.
    pareto.erase(std::remove_if(pareto.begin(), pareto.end(),
                                [&](const urllc::Candidate &c)
                                { return c.rbs > static_cast<int>(runtime.rbs); }),
                 pareto.end());

    if (pareto.empty())
    {
        throw InfeasibleFlowSet(
            app,
            "no transmission configuration satisfies app " + app +
                " (target packet error " + std::to_string(packet_error) +
                ", deadline " + std::to_string(deadline_ms) + " ms, " +
                std::to_string(static_cast<int>(runtime.rbs)) + " RBs)");
    }

    // Within A^max whenever possible: only when every configuration that fits
    // the cell needs more attempts does the rule fall back to them, and the
    // analysis then counts the run outside the model (ko_amax in
    // analysis/plot_paper.py). Dropping the configurations beyond A^max after
    // the Pareto filter loses nothing: whatever dominates a configuration
    // within A^max that fits the cell is itself within A^max and fits.
    const bool within_cap =
        std::any_of(pareto.begin(), pareto.end(), [&](const urllc::Candidate &c)
                    { return c.attempts <= runtime.a_max; });
    if (within_cap)
        pareto.erase(std::remove_if(pareto.begin(), pareto.end(),
                                    [&](const urllc::Candidate &c)
                                    { return c.attempts > runtime.a_max; }),
                     pareto.end());

    std::sort(pareto.begin(), pareto.end(),
              [](const urllc::Candidate &a, const urllc::Candidate &b)
              {
                  if (a.segments != b.segments)
                      return a.segments < b.segments;
                  if (a.attempts != b.attempts)
                      return a.attempts < b.attempts;
                  if (a.rbs != b.rbs)
                      return a.rbs < b.rbs;
                  return a.m > b.m;
              });
    return pareto.front();
}

void write_infeasible_report(const std::string &path, int combo_index,
                             const RuntimeConfig &runtime,
                             const std::vector<std::string> &apps,
                             const std::string &objective,
                             const std::string &reason)
{
    json report;
    report["combo_index"] = combo_index;
    report["config"] = {
        {"m_harq", runtime.harqs},
        {"n_rb", static_cast<int>(runtime.rbs)},
        {"tti_ms", runtime.tti_s * 1000.0},
        {"t_up_ms", upper_layer_delay_ms(runtime)},
        {"t_up_tti", upper_layer_delay_tti(runtime)},
        {"objective", objective}};
    report["schedulable"] = false;
    report["infeasible_flows"] = apps;
    report["infeasible_reason"] = reason;
    report["flows"] = json::array();
    report["dispatch_table"] = json::array();
    report["hyperperiod_ms"] = 0.0;
    report["hyperperiod_tti"] = 0;
    report["harq_used"] = 0;
    write_report(path, report);
}

void write_report(const std::string &path, const json &report)
{
    const auto parent = std::filesystem::path(path).parent_path();
    if (!parent.empty() && !std::filesystem::exists(parent))
    {
        die("Output directory does not exist: " + parent.string());
    }
    std::ofstream output(path);
    if (!output)
        die("Cannot open output file: " + path);
    output << std::setw(2) << report << '\n';
    if (!output)
        die("Failed while writing output: " + path);
}

std::vector<Job> materialize_jobs(
    const std::vector<Flow> &flows, const std::vector<FlowScheduleConfig> &flow_cfgs,
    double hyperperiod_ms, const RuntimeConfig &runtime)
{
    std::vector<Job> jobs;
    const AttemptSampler sampler(runtime.exec_sample_seed,
                                 sampling_execution_times(runtime));

    for (std::size_t fi = 0; fi < flows.size(); ++fi)
    {
        const auto &flow = flows[fi];
        const auto &cfg = flow_cfgs[fi];
        std::size_t instance = 0;
        for (int release = 0; release < hyperperiod_ms - kTimeEpsMs;
             release += flow.period_ms, ++instance)
        {
            if (instance > static_cast<std::size_t>(std::numeric_limits<int>::max()))
                die("Too many instances for app " + flow.app);
            const int deadline = release + flow.deadline_ms;
            for (std::size_t si = 0; si < cfg.segment_tbs.size(); ++si)
            {
                // job_id must identify the job, not its position in this
                // vector, so every method draws the same run for a seed.
                const std::uint64_t job_id =
                    (static_cast<std::uint64_t>(fi) << 40) ^
                    (static_cast<std::uint64_t>(instance) << 16) ^
                    static_cast<std::uint64_t>(si);
                const int attempts = sampler.Draw(cfg.bler, cfg.k, job_id);

                jobs.push_back(Job{
                    fi, cfg.k, instance, static_cast<int>(si), cfg.tbs, cfg.m, cfg.b,
                    attempts, cfg.bler,
                    release, deadline, cfg.b <= static_cast<int>(runtime.rbs)});
            }
        }
    }

    std::stable_sort(jobs.begin(), jobs.end(), [](const Job &a, const Job &b)
                     {
        if (std::abs(a.release_ms - b.release_ms) > kTimeEpsMs) return a.release_ms < b.release_ms;
        if (a.flow_index != b.flow_index) return a.flow_index < b.flow_index;
        if (a.instance_index != b.instance_index) return a.instance_index < b.instance_index;
        return a.segment_index < b.segment_index; });
    return jobs;
}

int extract_combo_index(const std::string &flowset_path)
{
    const std::string stem = std::filesystem::path(flowset_path).stem().string();
    const std::string prefix = "flowset_";
    if (stem.rfind(prefix, 0) != 0)
        die("Flowset filename must follow flowset_<integer>.json: " + flowset_path);
    const std::string suffix = stem.substr(prefix.size());
    if (suffix.empty() || !std::all_of(suffix.begin(), suffix.end(), [](unsigned char c)
                                       { return std::isdigit(c) != 0; }))
    {
        die("Flowset filename must follow flowset_<integer>.json: " + flowset_path);
    }
    try
    {
        const unsigned long long value = std::stoull(suffix);
        if (value > static_cast<unsigned long long>(std::numeric_limits<int>::max()))
            die("combo_index exceeds signed int range");
        return static_cast<int>(value);
    }
    catch (...)
    {
        die("Invalid numeric flowset index: " + suffix);
    }
}