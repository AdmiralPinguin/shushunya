package com.shushunya.m.wear.data;

/** Bounded retry delays for a cold Wear Data Layer topology. */
final class NearbyTargetRetryPolicy {
    private static final long[] RETRY_DELAYS_MS = {
            250L, 500L, 750L, 1_500L, 3_000L
    };

    private NearbyTargetRetryPolicy() {}

    static long delayAfterFailure(int failureIndex) {
        return failureIndex >= 0 && failureIndex < RETRY_DELAYS_MS.length
                ? RETRY_DELAYS_MS[failureIndex]
                : -1L;
    }
}
