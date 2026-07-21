package com.shushunya.m.wear.data;

/** Fixed, bounded retry budget for one standalone MUSIC command. */
public final class MusicRetryPolicy {
    public static final long BUDGET_MS = 8_000L;
    private static final long[] OFFSETS_MS = {
            0L, 250L, 750L, 1_500L, 3_000L, 5_000L, 7_500L
    };

    private MusicRetryPolicy() {}

    static int attemptCount() {
        return OFFSETS_MS.length;
    }

    static long offsetMs(int attemptIndex) {
        return attemptIndex >= 0 && attemptIndex < OFFSETS_MS.length
                ? OFFSETS_MS[attemptIndex]
                : -1L;
    }

    static long delayUntilAttempt(
            long startedAtMs,
            int attemptIndex,
            long nowMs) {
        long offset = offsetMs(attemptIndex);
        if (startedAtMs <= 0L || offset < 0L || nowMs < startedAtMs) return -1L;
        long dueAt = saturatedAdd(startedAtMs, offset);
        return Math.max(0L, dueAt - nowMs);
    }

    static boolean isAlive(long startedAtMs, long deadlineAtMs, long nowMs) {
        return startedAtMs > 0L
                && deadlineAtMs == saturatedAdd(startedAtMs, BUDGET_MS)
                && nowMs >= startedAtMs
                && nowMs < deadlineAtMs;
    }

    static long deadlineAt(long startedAtMs) {
        return startedAtMs <= 0L ? 0L : saturatedAdd(startedAtMs, BUDGET_MS);
    }

    private static long saturatedAdd(long left, long right) {
        if (right > 0L && left > Long.MAX_VALUE - right) return Long.MAX_VALUE;
        return left + right;
    }
}
