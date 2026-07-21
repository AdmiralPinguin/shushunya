package com.shushunya.m.wear.audio;

/**
 * Bounded startup policy for distinguishing a live microphone from a stale
 * AudioRecord route that only returns digital zeroes.
 *
 * <p>A real open microphone has a non-zero noise floor even while nobody is
 * speaking. Therefore this policy deliberately tests exact sample equality,
 * not an RMS speech threshold. It allows one full route reacquisition and then
 * fails closed; it never selects another microphone.</p>
 */
final class ZeroPcmStartupGuard {
    static final int DEFAULT_ZERO_FRAME_LIMIT = 75; // 1.5 s at 20 ms/frame
    static final int DEFAULT_MAX_REACQUISITIONS = 1;

    enum Decision {
        WAIT,
        PROVEN,
        REACQUIRE,
        FAIL
    }

    private final int zeroFrameLimit;
    private final int maximumReacquisitions;
    private int consecutiveZeroFrames;
    private int reacquisitions;
    private boolean proven;

    ZeroPcmStartupGuard() {
        this(DEFAULT_ZERO_FRAME_LIMIT, DEFAULT_MAX_REACQUISITIONS);
    }

    ZeroPcmStartupGuard(int zeroFrameLimit, int maximumReacquisitions) {
        if (zeroFrameLimit <= 0) throw new IllegalArgumentException("zeroFrameLimit <= 0");
        if (maximumReacquisitions < 0) {
            throw new IllegalArgumentException("maximumReacquisitions < 0");
        }
        this.zeroFrameLimit = zeroFrameLimit;
        this.maximumReacquisitions = maximumReacquisitions;
    }

    Decision observe(short[] samples) {
        if (samples == null || samples.length == 0) {
            throw new IllegalArgumentException("empty PCM frame");
        }
        if (proven) return Decision.PROVEN;
        for (short sample : samples) {
            if (sample != 0) {
                proven = true;
                consecutiveZeroFrames = 0;
                return Decision.PROVEN;
            }
        }
        consecutiveZeroFrames++;
        if (consecutiveZeroFrames < zeroFrameLimit) return Decision.WAIT;
        consecutiveZeroFrames = 0;
        if (reacquisitions < maximumReacquisitions) {
            reacquisitions++;
            return Decision.REACQUIRE;
        }
        return Decision.FAIL;
    }

    int reacquisitions() {
        return reacquisitions;
    }
}
