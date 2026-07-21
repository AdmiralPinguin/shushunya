package com.shushunya.m.wear.audio;

/** Bounded retry budget that fits inside the phone's eight-second recovery gate. */
final class ChannelReconnectPolicy {
    static final int MAX_REPLACEMENT_ATTEMPTS = 3;
    static final long TOTAL_BUDGET_MS = 7_000L;
    static final long ATTEMPT_TASK_TIMEOUT_MS = 1_000L;
    private static final long[] BACKOFF_MS = {150L, 350L, 750L};

    private ChannelReconnectPolicy() {}

    static long deadline(long firstFailureElapsedMs) {
        if (firstFailureElapsedMs <= 0L) throw new IllegalArgumentException("invalid failure time");
        return safeAdd(firstFailureElapsedMs, TOTAL_BUDGET_MS);
    }

    static boolean mayAttempt(int attemptIndex, long nowElapsedMs, long deadlineElapsedMs) {
        return attemptIndex >= 0
                && attemptIndex < MAX_REPLACEMENT_ATTEMPTS
                && nowElapsedMs > 0L
                && nowElapsedMs < deadlineElapsedMs;
    }

    static long backoffMs(int attemptIndex) {
        if (attemptIndex < 0 || attemptIndex >= BACKOFF_MS.length) return -1L;
        return BACKOFF_MS[attemptIndex];
    }

    static long taskTimeoutMs(long nowElapsedMs, long deadlineElapsedMs) {
        long remaining = deadlineElapsedMs - nowElapsedMs;
        return Math.max(0L, Math.min(ATTEMPT_TASK_TIMEOUT_MS, remaining));
    }

    private static long safeAdd(long first, long second) {
        return first > Long.MAX_VALUE - second ? Long.MAX_VALUE : first + second;
    }
}
