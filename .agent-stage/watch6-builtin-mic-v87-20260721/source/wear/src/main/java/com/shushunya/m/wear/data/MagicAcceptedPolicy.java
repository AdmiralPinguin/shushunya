package com.shushunya.m.wear.data;

import java.util.Set;

/** Pure correlation rules for the phone bridge's post-dispatch ACCEPTED ACK. */
final class MagicAcceptedPolicy {
    static final long MAX_ACK_AGE_MS = 35_000L;

    private MagicAcceptedPolicy() {}

    static boolean matches(
            String expectedRequestId,
            Set<String> allowedNodeIds,
            String actualRequestId,
            String actualNodeId,
            long acknowledgedAtElapsedMs,
            long nowElapsedMs) {
        String expected = clean(expectedRequestId, 160);
        String actual = clean(actualRequestId, 160);
        String source = clean(actualNodeId, 256);
        long ageMs = nowElapsedMs - acknowledgedAtElapsedMs;
        return !expected.isEmpty()
                && expected.equals(actual)
                && allowedNodeIds != null
                && allowedNodeIds.contains(source)
                && acknowledgedAtElapsedMs > 0L
                && ageMs >= 0L
                && ageMs <= MAX_ACK_AGE_MS;
    }

    static boolean isStale(long acknowledgedAtElapsedMs, long nowElapsedMs) {
        return acknowledgedAtElapsedMs <= 0L
                || nowElapsedMs < acknowledgedAtElapsedMs
                || nowElapsedMs - acknowledgedAtElapsedMs > MAX_ACK_AGE_MS;
    }

    static String cleanRequestId(String value) {
        return clean(value, 160);
    }

    static String cleanNodeId(String value) {
        return clean(value, 256);
    }

    private static String clean(String value, int maxLength) {
        String clean = value == null ? "" : value.trim();
        return clean.length() > maxLength ? clean.substring(0, maxLength) : clean;
    }
}
