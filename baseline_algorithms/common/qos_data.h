#ifndef QOS_DATA_H
#define QOS_DATA_H

#include <string>
#include <vector>
#include <cstdint>

#define MIN_NGBR_DLP 5
#define MAX_NGBR_DLP 90
#define MIN_GBR_LOG_PER -8
#define MAX_GBR_LOG_PER -2

class qos_data
{
public:
    struct QoS5G
    {
        int fiveQI;
        std::string resource_type;
        float default_priority_level;
        int packet_delay_budget;
        float packet_error_rate_exp;
        int default_max_data_burst_volume;
        int default_averaging_window;
        std::string example_services;
    };

    // Based on 3GPP TS 23.501 v19.4.0, Table 5.7.4-1
    const std::vector<QoS5G> QoS5GTable = {
        {1, "GBR", 20, 100, -2, -1, 2000, "Conversational Voice"},
        {2, "GBR", 40, 150, -3, -1, 2000, "Conversational Video (Live Streaming)"},
        {3, "GBR", 30, 50, -3, -1, 2000, "Real Time Gaming, V2X messages, Electricity distribution medium voltage, Process automation monitoring"},
        {4, "GBR", 50, 300, -6, -1, 2000, "Non-Conversational Video (Buffered Streaming)"},
        {65, "GBR", 7, 75, -2, -1, 2000, "Mission Critical user plane Push To Talk voice (e.g., MCPTT)"},
        {66, "GBR", 20, 100, -2, -1, 2000, "Non-Mission-Critical user plane Push To Talk voice"},
        {67, "GBR", 15, 100, -3, -1, 2000, "Mission Critical Video user plane"},
        {75, "GBR", 25, 50, -2, -1, 2000, "A2X messages"}, // NOTE 14: This 5QI is not supported in this Release of the specification as it is only used for transmission of V2X
        {71, "GBR", 56, 150, -6, -1, 2000, "Live Uplink Streaming (e.g. TS 26.238)"},
        {72, "GBR", 56, 300, -4, -1, 2000, "Live Uplink Streaming (e.g. TS 26.238)"},
        {73, "GBR", 56, 300, -8, -1, 2000, "Live Uplink Streaming (e.g. TS 26.238)"},
        {74, "GBR", 56, 500, -8, -1, 2000, "Live Uplink Streaming (e.g. TS 26.238)"},
        {76, "GBR", 56, 500, -4, -1, 2000, "Live Uplink Streaming (e.g. TS 26.238)"},
        {5, "NGBR", 10, 100, -6, -1, -1, "IMS Signalling"},
        {6, "NGBR", 60, 300, -6, -1, -1, "Video (Buffered Streaming) TCP-based"},
        {7, "NGBR", 70, 100, -3, -1, -1, "Voice, Video (Live Streaming), Interactive Gaming"},
        {8, "NGBR", 80, 300, -6, -1, -1, "Video (Buffered Streaming) TCP-based"},
        {9, "NGBR", 90, 300, -6, -1, -1, "Video (Buffered Streaming) TCP-based"},
        {69, "NGBR", 5, 60, -6, -1, -1, "Mission Critical delay sensitive signalling (e.g., MC-PTT signalling)"},
        {70, "NGBR", 55, 200, -6, -1, -1, "Mission Critical Data"},
        {79, "NGBR", 65, 50, -2, -1, -1, "V2X messages"},
        {80, "NGBR", 68, 10, -6, -1, -1, "Low Latency eMBB applications Augmented Reality"},
        {82, "DCGBR", 19, 10, -4, 255, 2000, "Discrete Automation"},
        {83, "DCGBR", 22, 10, -4, 1354, 2000, "Discrete Automation, V2X messages"},
        {84, "DCGBR", 24, 30, -5, 1354, 2000, "Intelligent transport systems"},
        {85, "DCGBR", 21, 5, -5, 255, 2000, "Electricity Distribution-high voltage, V2X messages"},
        {86, "DCGBR", 18, 5, -4, 1354, 2000, "V2X messages"},
        {87, "DCGBR", 25, 5, -3, 500, 2000, "Interactive Service - Motion tracking data"},
        {88, "DCGBR", 25, 10, -3, 1125, 2000, "Interactive Service - Motion tracking data"},
        {89, "DCGBR", 25, 15, -4, 17000, 2000, "Visual content for cloud/edge/split rendering"},
        {90, "DCGBR", 25, 20, -4, 63000, 2000, "Visual content for cloud/edge/split rendering"},

    };
};

const qos_data::QoS5G get_qos_parameters(int qos_id);

#endif // QOS_DATA_H
