package com.shushunya.m.wear.data;

/** Host-testable retry/ACK policy; transport queue success is deliberately irrelevant. */
public final class StartupFailureDeliveryPolicy {
    private static final long[] DELAYS_MS = {
            0L, 250L, 750L, 1_500L, 3_000L, 6_000L, 12_000L
    };

    private StartupFailureDeliveryPolicy() {}

    public static long delayForAttempt(int attempt) {
        if (attempt < 0 || attempt >= DELAYS_MS.length) return -1L;
        return DELAYS_MS[attempt];
    }

    public static boolean shouldRetry(int completedAttempts, boolean applicationAcked) {
        return !applicationAcked
                && completedAttempts >= 0
                && completedAttempts < DELAYS_MS.length;
    }

    public static int nextAttempt(int completedAttempt) {
        if (completedAttempt < 0) return 0;
        return completedAttempt >= DELAYS_MS.length
                ? DELAYS_MS.length
                : completedAttempt + 1;
    }

    public static boolean isExhausted(int nextAttempt) {
        return nextAttempt >= DELAYS_MS.length;
    }

    public static boolean sameDelivery(
            String expectedNode,
            String expectedRequest,
            String expectedCode,
            long expectedFailedAtMs,
            String sourceNode,
            String request,
            String code,
            long failedAtMs) {
        return !clean(expectedNode).isEmpty()
                && clean(expectedNode).equals(clean(sourceNode))
                && clean(expectedRequest).equals(clean(request))
                && clean(expectedCode).equals(clean(code))
                && expectedFailedAtMs > 0L
                && expectedFailedAtMs == failedAtMs;
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
