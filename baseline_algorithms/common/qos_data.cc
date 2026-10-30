#include "qos_data.h"
#include <algorithm> // for std::find_if

const qos_data::QoS5G get_qos_parameters(int qos_id)
{
    qos_data qos;

    // Compares each QoS5G struct's fiveQI with the provided qos_id
    // Returns true when it finds a match
    auto it = std::find_if(qos.QoS5GTable.begin(), qos.QoS5GTable.end(),
                           [qos_id](const qos_data::QoS5G &params)
                           {
                               return params.fiveQI == qos_id;
                           });

    // If found (it's not end()), returns the found QoS5G struct
    // If not found, returns empty QoS5G struct
    return (it != qos.QoS5GTable.end()) ? *it : qos_data::QoS5G{};
}
