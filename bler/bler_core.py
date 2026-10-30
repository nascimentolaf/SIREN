import sys
import numpy as np
import time
from multiprocessing import Pool, cpu_count

from scipy.special import j0
from scipy.linalg import toeplitz
import scipy.constants as const

# Why do we consider single path in Jake's here?
# Reduce complexity and enough if we assume the following:
# A TBS fits within the coherence bandwidth of the channel
# We assume the worst-case SNR and we benchmark the LDPC code's time-diversity performance, not frequency diversity

def simulate_chunk(chunk_size, TBS, R, modulation_order, snr_db, channel_model, L_jakes):
    """
    Simulate a chunk of LDPC block transmissions for a single SNR point.

    Encodes, modulates, transmits through a channel, demodulates, and decodes
    a fixed number of transport blocks.

    All TensorFlow and Sionna imports are deferred to inside this function so
    that they are initialised fresh in each worker process after the fork,
    avoiding TF runtime state inheritance that causes deadlocks.

    The encoder and decoder are constructed ONCE per worker call (outside the
    per-block loop) and reused across all blocks in the chunk.

    Parameters
    ----------
    chunk_size      : Number of transport blocks to simulate in this chunk.
    TBS             : Transport block size in bits, a positive integer.
    R               : Target code rate, 0 < R < 1.
    modulation_order: Number of bits per symbol (e.g., 2 for QPSK, 4 for 16-QAM, 6 for 64-QAM).
    snr_db          : Signal-to-noise ratio in dB for this simulation point.
    channel_model   : Channel model to use: either "awgn" or "jakes".
                      For Jakes, perfect equalization is assumed.
    L_jakes         : Precomputed Cholesky factor of the Jakes covariance matrix.
                      Must be provided when channel_model is "jakes", None otherwise.

    Returns
    -------
    num_errors      : Number of block errors observed across the chunk. A block error
                      is declared if any single bit in the decoded transport block
                      differs from the transmitted transport block (TB-level decision).
    """
    # ---------------------------------------------------------------------------
    # Deferred imports: initialised fresh in each worker process after fork,
    # preventing TF internal lock/mutex inheritance that causes deadlocks.
    # ---------------------------------------------------------------------------
    import tensorflow as tf
    from sionna.phy.mapping import Mapper, Demapper
    from sionna.phy.channel import AWGN
    from sionna.phy.nr import TBEncoder, TBDecoder

    channel_model = channel_model.lower()
    maximum_allowed_tbs = 1053 * 8

    if TBS > maximum_allowed_tbs: # TBS=8424 bits https://www.sharetechnote.com/html/5G/5G_MCS_TBS_CodeRate.html
        msg = f"\n[ERROR] TBS={TBS} bits exceeds the maximum of {maximum_allowed_tbs} bits supported by LDPC5GEncoder. See https://github.com/NVlabs/sionna/issues/1001).\n"
        sys.exit(msg)

    # Modulation
    mapper = Mapper("qam", modulation_order)
    demapper = Demapper("app", "qam", modulation_order)

    # LDPC (5G NR compliant)

    # Encoder code rate to 5G NR minimum (1/5)
    MIN_CODE_RATE = 1 / 5
    R_enc = max(R, MIN_CODE_RATE)

    # Actual number of bits to transmit (target code rate R)
    n_tx = int(np.ceil(TBS / R))
    n_tx = int(np.ceil(n_tx / modulation_order) * modulation_order)

    # Encoder output length capped at 5*TBS
    n_enc = int(np.ceil(TBS / R_enc))
    n_enc = int(np.ceil(n_enc / modulation_order) * modulation_order)
   
    encoder = TBEncoder(target_tb_size=TBS, num_coded_bits=n_enc, target_coderate=R_enc, num_bits_per_symbol=modulation_order, channel_type="PDSCH", device="cpu")
    decoder = TBDecoder(encoder, num_bp_iter=4)

    # Noise variance (constant across the chunk)
    snr_linear = 10**(snr_db / 10)
    noise_var  = tf.constant(1 / snr_linear, dtype=tf.float32)

    # AWGN channel helper
    awgn_channel = AWGN() if channel_model == "awgn" else None

    # Monte-Carlo loop over blocks in the chunk
    num_errors = 0
    for _ in range(chunk_size):
        bits = np.random.randint(0, 2, TBS) # one full transport block

        # Encode: shape(1, n_enc)
        c = encoder(tf.constant(bits.reshape(1, -1), dtype=tf.float32))

        # Circular buffer repetition - see TS 38.212 Section 5.4.2 (rate matching)
        c_np = c.numpy().flatten()
        if n_tx > n_enc:
            reps = int(np.ceil(n_tx / n_enc)) # how many full passes needed
            c_np = np.tile(c_np, reps)[:n_tx]
        c = tf.constant(c_np.reshape(1, -1), dtype=tf.float32)

        # Modulate
        x = mapper(c)

        # Channel
        if channel_model == "awgn":
            y = awgn_channel(x, noise_var)
            effective_noise_var = noise_var
        elif channel_model == "jakes": # see https://arxiv.org/pdf/2010.12166
            n_symbols = x.shape[-1]

            # Generate correlated Rayleigh fading using precomputed Cholesky factor
            h_real = L_jakes @ np.random.randn(n_symbols) / np.sqrt(2)
            h_imag = L_jakes @ np.random.randn(n_symbols) / np.sqrt(2)
            h = tf.constant((h_real + 1j * h_imag).reshape(1, -1), dtype=tf.complex64)

            # Apply fading + AWGN
            noise_std = tf.sqrt(noise_var / 2)
            noise = tf.complex(
                tf.random.normal(tf.shape(x), stddev=noise_std),
                tf.random.normal(tf.shape(x), stddev=noise_std)
            )
            y = tf.cast(x, tf.complex64) * h + noise
            y = y / h  # perfect equalization
            h_power = tf.reduce_mean(tf.abs(h) ** 2)
            effective_noise_var = noise_var / h_power
        else:
            raise ValueError(f"Unknown channel model: {channel_model}")

        # Demodulate (log-likelihood ratio)
        llr = demapper(y, effective_noise_var)

        # --- Combine repeated LLRs before decoding ---
        llr_np = llr.numpy().flatten()
        if n_tx > n_enc:
            llr_combined = np.zeros(n_enc)
            for i in range(n_tx):
                llr_combined[i % n_enc] += llr_np[i]   # LLR combining
            llr = tf.constant(llr_combined.reshape(1, -1), dtype=tf.float32)

        # Decode
        _, tb_crc = decoder(llr)
        if not tb_crc.numpy().flatten()[0]:
            num_errors += 1

    return num_errors


def get_rbs(tbs, modulation_order, code_rate):
    """
    Compute the number of resource blocks (RBs) needed to transmit a transport block of size `tbs` bits given the modulation order and code rate.

    Parameters
    ----------
    tbs             : Transport block size, a positive integer.
    modulation_order: Modulation order (bits per symbol, e.g., 2 for QPSK, 4 for 16-QAM).
    code_rate       : Target code rate, 0 < code_rate < 1.

    Returns
    -------
    num_rbs              : Number of resource blocks derived from system parameters.
    """
    NREoverhead = 12                        # DMRS
    Nsc = 12                                # subcarriers per RB
    Nsym = 14                               # symbols per slot (normal CP)
    NREperRB = Nsc * Nsym - NREoverhead     # e.g., 168 - 12 = 156
    nlayers = 1                             # number of layers

    bitsPerRB = NREperRB * modulation_order * nlayers
    outlen = int(np.ceil(tbs / code_rate))
    num_rbs = int(np.ceil(outlen / bitsPerRB))
    
    return num_rbs

def helper_ldpc_bler_sim(TBS, M, R, snrdBvec, targetNumBlockErrors, maxNumBlocksPerWorker, numBlocksInsideParfor, scs, fc, speed, channelModel, skip_next_bler):
    """
    Simulate LDPC BLER for a 5G NR downlink shared channel (DL-SCH) using parallel workers.

    For each SNR value in `snrdBvec`, Monte Carlo block transmissions are executed in parallel
    using a multiprocessing pool. Simulation proceeds in chunks until either a target number
    of block errors is reached or a maximum number of simulated blocks per worker is exceeded.

    Each parallel iteration assigns the same number of blocks to all workers, ensuring
    balanced workloads and minimizing idle time in the pool.
    Parameters
    ----------
    TBS                     : Transport block size, a positive integer.
    M                       : Modulation order (bits per symbol, e.g., 2 for QPSK).
    R                       : Target code rate, 0 < R < 1.
    snrdBvec                : Vector of SNR values in dB.
    targetNumBlockErrors    : Target number of block errors per SNR point.
    maxNumBlocksPerWorker   : Maximum blocks each worker may simulate per SNR point.
    numBlocksInsideParfor   : Blocks simulated per worker per parallel iteration.
    scs                     : Subcarrier spacing in Hz (e.g., 15e3). Jakes only.
    fc                      : Carrier frequency in Hz (e.g., 2.6e9). Jakes only.
    speed                   : UE velocity in m/s. Jakes only.
    channelModel            : "awgn" or "jakes".
    skip_next_bler          : If True, skip simulation and return all-ones BLER.

    Returns
    -------
    BLER                : Estimated block error rate for each SNR value.
    finalNumBlockErrors : Total block errors observed per SNR point.
    finalNumBlocks      : Total simulated blocks per SNR point.
    executionTime       : Wall-clock execution time per SNR point.
    """
    n_workers = cpu_count()

    BLER                = np.zeros(len(snrdBvec))
    executionTime       = np.zeros(len(snrdBvec))
    finalNumBlockErrors = np.zeros(len(snrdBvec))
    finalNumBlocks      = np.zeros(len(snrdBvec))

    outlen = int(np.ceil(TBS / R))

    if not skip_next_bler:
        # Precompute Cholesky decomposition for Jakes model
        if channelModel.lower() == "jakes":
            symbol_duration = 1 / scs
            doppler_freq    = speed * fc / const.c
            n_symbols       = int(np.ceil(outlen / M))
            delta_t         = symbol_duration * np.arange(n_symbols)
            autocorr        = j0(2 * np.pi * doppler_freq * delta_t)
            L_jakes         = np.linalg.cholesky(toeplitz(autocorr) + 1e-9 * np.eye(n_symbols))
        else:
            L_jakes = None

        for idx, snr in enumerate(snrdBvec):
            all_errors = 0
            all_blocks = 0
            chunk_size = numBlocksInsideParfor[idx]

            start_time = time.time()
            while all_errors < targetNumBlockErrors[idx] and all_blocks < maxNumBlocksPerWorker[idx] * n_workers:

                args = [
                    (chunk_size, TBS, R, M, snr, channelModel, L_jakes)
                    for _ in range(n_workers)
                ]

                with Pool(n_workers) as pool:
                    results = pool.starmap(simulate_chunk, args)

                all_errors += sum(results)
                all_blocks += chunk_size * n_workers

                print(f"SNR={snr} dB | workers={n_workers} | blocks={all_blocks} | errors={all_errors} | "f"elapsed={time.time()-start_time:.2f}s")

            executionTime[idx]       = time.time() - start_time
            BLER[idx]                = all_errors / all_blocks
            finalNumBlockErrors[idx] = all_errors
            finalNumBlocks[idx]      = all_blocks
    else:
        BLER = np.ones(len(snrdBvec), dtype=float)

    return BLER, finalNumBlockErrors, finalNumBlocks, executionTime