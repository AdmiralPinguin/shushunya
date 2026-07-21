package com.shushunya.m.wear.audio;

/**
 * Watch-side sub-budgets inside the phone's non-resetting eight-second gate.
 * The phone's final drain resend is scheduled at 1500 ms, so even that delivery
 * plus a one-second PCM drain and the full five-second ACK window leaves 500 ms.
 */
final class CrossDeviceDrainBudgetPolicy {
    static final long PHONE_GATE_MS = 8_000L;
    static final long PHONE_LAST_DRAIN_RESEND_MS = 1_500L;
    static final long WATCH_TOTAL_FROM_ACCEPT_MS = 6_000L;
    static final long PCM_DRAIN_MS = 1_000L;
    static final long TERMINAL_ACK_MS = 5_000L;

    private CrossDeviceDrainBudgetPolicy() {}

    static long acceptedDeadlineMs(long acceptedAtElapsedMs) {
        if (acceptedAtElapsedMs <= 0L) return 0L;
        return safeAdd(acceptedAtElapsedMs, WATCH_TOTAL_FROM_ACCEPT_MS);
    }

    static long ackBudgetMs(long acceptedAtElapsedMs, long nowElapsedMs) {
        if (acceptedAtElapsedMs <= 0L) return TERMINAL_ACK_MS;
        long remaining = acceptedDeadlineMs(acceptedAtElapsedMs) - nowElapsedMs;
        return Math.max(0L, Math.min(TERMINAL_ACK_MS, remaining));
    }

    static long bindingWaitBudgetMs(
            long requestedWaitMs,
            long acceptedAtElapsedMs,
            long nowElapsedMs) {
        if (acceptedAtElapsedMs <= 0L) return Math.max(0L, requestedWaitMs);
        long remaining = acceptedDeadlineMs(acceptedAtElapsedMs) - nowElapsedMs;
        return Math.max(0L, Math.min(requestedWaitMs, remaining));
    }

    static long worstCasePhoneMarginMs() {
        return PHONE_GATE_MS
                - PHONE_LAST_DRAIN_RESEND_MS
                - PCM_DRAIN_MS
                - TERMINAL_ACK_MS;
    }

    private static long safeAdd(long first, long second) {
        return first > Long.MAX_VALUE - second ? Long.MAX_VALUE : first + second;
    }
}
