import sys
import json
from bler_core import helper_ldpc_bler_sim, get_rbs
from sionna.phy.nr.utils import decode_mcs_index     
from multiprocessing import set_start_method                                                                                                                                                                                                                    
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"  # hide all GPUs from CUDA
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"   # suppresses log from TF backend (I do not want to use GPU)

# Simulation
def run_simulation(tbs, worst_case_snr_db, scs, fc, speed, channel_model, min_mcs, max_mcs, mcs_table, target_num_block_errors, num_blocks_inside_parfor, max_num_blocks_per_worker):
    results = []
    
    if mcs_table == 2 and max_mcs >= 28:
        max_mcs = 27    

    skip_next_bler = False
    for mcs_index in range(min_mcs, max_mcs+1):

        modulation_order, code_rate = decode_mcs_index(mcs_index, mcs_table)        
        
        num_rbs = get_rbs(tbs, modulation_order, code_rate)
        
        print(f"MCS = {mcs_index} | {num_rbs} RBs")
        
        BLER, finalNumBlockErrors, finalNumBlocks, executionTime = helper_ldpc_bler_sim(
            TBS=tbs,
            M=modulation_order,
            R=code_rate,
            snrdBvec=worst_case_snr_db,
            targetNumBlockErrors=target_num_block_errors,
            maxNumBlocksPerWorker=max_num_blocks_per_worker,
            numBlocksInsideParfor=num_blocks_inside_parfor,
            scs=scs,
            fc=fc,
            speed=speed,
            channelModel=channel_model,
            skip_next_bler = skip_next_bler
        )


        entry = {
            "mcs_index": mcs_index,
            "bler": float(BLER[0]),
            "num_rbs": int(num_rbs),
            "snr_db": float(worst_case_snr_db[0]),
            "modulation_order": int(modulation_order),
            "code_rate": float(code_rate),
            "execution_time_s": float(executionTime[0]),
            "num_block_errors": int(finalNumBlockErrors[0]),
            "num_blocks": int(finalNumBlocks[0]),
            "mcs_table": mcs_table,
        }
        
        results.append(entry)
        print(f"BLER={entry['bler']:.4f} | "f"RBs={entry['num_rbs']} | time={entry['execution_time_s']:.2f}s")
        
        if float(BLER[0]) == 1.0:
            skip_next_bler = True

    # save JSON
    with open(f"data/{channel_model}_{int(tbs/8)}_mcs_bler_results_{worst_case_snr_db[0]}.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    # Simulation Parameters
    # target_tbs_list = [66*8, 129*8, 261*8, 469*8]        # Transport block size (bits), a positive integer
    target_tbs_list = [129*8]                          # Transport block size (bits), a positive integer
    snr_db_list = [10.0]                                 # Worst-case Subcarrier SINR (dB)

    mcs_table = 1                                        # MCS index table for PDSCH, 1-3
    min_mcs = 0
    max_mcs = 28

    scs = 60e3                                            # 15, 30, 60, 120, 240, 480, 960 [necessary for jakes]
    fc = 3.8e9                                            # carrier frequency in GHz [necessary for jakes]
    speed = 3                                             # UE speed in m/s [necessary for jakes]

    channel_model = sys.argv[1] if len(sys.argv) > 1 else "awgn"    # "awgn" or "jakes"

    target_num_block_errors = [100]
    num_blocks_inside_parfor = [1000]
    max_num_blocks_per_worker = [1e8]

    set_start_method("spawn")
    # Run every combination of TBS and SNR
    for target_tbs in target_tbs_list:
        for worst_case_snr_db in snr_db_list:

            print(
                f"=================   "
                f"Running simulation: "
                f"TBS={target_tbs} bits, "
                f"SNR={worst_case_snr_db:.1f} dB, "
                f"channel={channel_model}"
                f"   ================="
            )

            run_simulation(target_tbs, [worst_case_snr_db], scs, fc, speed, channel_model, min_mcs, max_mcs, mcs_table, target_num_block_errors, num_blocks_inside_parfor, max_num_blocks_per_worker)


    print("The simulation has been finished")
