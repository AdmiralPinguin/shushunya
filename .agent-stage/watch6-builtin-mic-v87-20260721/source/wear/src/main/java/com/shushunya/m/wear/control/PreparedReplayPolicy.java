package com.shushunya.m.wear.control;

/** Same-request PREPARE schedule after the phone wake has been queued. */
final class PreparedReplayPolicy {
    private static final long[] DELAYS_MS = {
            0L, 250L, 750L, 1_500L, 3_000L, 5_000L, 8_000L, 12_000L
    };

    private PreparedReplayPolicy() {}

    static long[] delaysMs() {
        return DELAYS_MS.clone();
    }
}
