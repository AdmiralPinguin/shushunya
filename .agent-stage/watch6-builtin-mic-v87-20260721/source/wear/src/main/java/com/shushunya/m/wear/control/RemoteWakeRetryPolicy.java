package com.shushunya.m.wear.control;

/** Bounded same-request RemoteActivity wake schedule, relative to first wake. */
final class RemoteWakeRetryPolicy {
    static final long PRE_ACCEPT_WINDOW_MS = 26_000L;
    static final long GLOBAL_PRE_ACCEPT_FRESHNESS_MS = 30_000L;
    private static final long[] DELAYS_MS = {
            0L, 800L, 1_800L, 3_500L, 6_000L, 9_500L, 14_000L,
            19_000L, 24_000L, 31_000L, 39_000L, 48_000L, 57_000L
    };

    private RemoteWakeRetryPolicy() {}

    static long[] delaysMs() {
        return DELAYS_MS.clone();
    }

    static boolean shouldAttempt(
            boolean accepted,
            boolean preAcceptTimedOut,
            boolean exactTerminalPending,
            int attemptIndex) {
        // ACCEPTED means only that the phone service owns the explicit SET.
        // It must not suppress recovery while that exact UUID still awaits its
        // correlated terminal state. The pre-ACCEPTED deadline remains fatal.
        return !preAcceptTimedOut
                && exactTerminalPending
                && attemptIndex >= 0
                && attemptIndex < DELAYS_MS.length;
    }

    /**
     * After a process restart, perform one immediate catch-up attempt and skip
     * older schedule points instead of bursting every missed wake at once.
     */
    static int nextIndexAfterAttempt(int attemptIndex, long elapsedMs) {
        int next = Math.max(0, attemptIndex + 1);
        while (next < DELAYS_MS.length && DELAYS_MS[next] <= elapsedMs) next++;
        return next;
    }

    static long delayForIndex(int attemptIndex) {
        return attemptIndex >= 0 && attemptIndex < DELAYS_MS.length
                ? DELAYS_MS[attemptIndex]
                : -1L;
    }

    static long preAcceptDeadlineMs(long issuedAtMs, long wakeStartedAtMs) {
        return Math.min(
                safeAdd(issuedAtMs, GLOBAL_PRE_ACCEPT_FRESHNESS_MS),
                safeAdd(wakeStartedAtMs, PRE_ACCEPT_WINDOW_MS));
    }

    static boolean preAcceptTimedOut(
            boolean accepted,
            long issuedAtMs,
            long wakeStartedAtMs,
            long nowMs) {
        return !accepted
                && nowMs >= preAcceptDeadlineMs(issuedAtMs, wakeStartedAtMs);
    }

    static boolean mayScheduleAttempt(
            boolean accepted,
            long issuedAtMs,
            long wakeStartedAtMs,
            int attemptIndex) {
        long delayMs = delayForIndex(attemptIndex);
        if (delayMs < 0L) return false;
        if (accepted) return true;
        // Leave transport time before the phone's inclusive freshness edge.
        return safeAdd(wakeStartedAtMs, delayMs)
                < safeAdd(issuedAtMs, GLOBAL_PRE_ACCEPT_FRESHNESS_MS);
    }

    private static long safeAdd(long left, long right) {
        if (left > Long.MAX_VALUE - right) return Long.MAX_VALUE;
        return left + right;
    }

    static boolean isSemanticAcceptance(
            boolean remoteIntentSent,
            boolean exactAcceptedAckObserved) {
        // RemoteActivity Future success is transport-only (INTENT_SENT).
        return exactAcceptedAckObserved;
    }
}
