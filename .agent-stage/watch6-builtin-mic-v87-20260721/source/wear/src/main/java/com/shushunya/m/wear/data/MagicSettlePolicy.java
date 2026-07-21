package com.shushunya.m.wear.data;

import java.util.Locale;

/** Pure rules for final-start haptics and the non-extending STOP settle lock. */
final class MagicSettlePolicy {
    static final long SETTLE_MS = 2_000L;

    private MagicSettlePolicy() {}

    static boolean isExactStartedState(
            boolean matchingCommand,
            boolean stateApplied,
            boolean hasError,
            String liveState,
            boolean hasMagicState,
            boolean magicEngaged) {
        String state = liveState == null
                ? ""
                : liveState.trim().toLowerCase(Locale.ROOT);
        return matchingCommand
                && stateApplied
                && !hasError
                && "running".equals(state)
                && hasMagicState
                && magicEngaged;
    }

    static long lockUntil(long confirmedAtMs) {
        if (confirmedAtMs <= 0L || confirmedAtMs > Long.MAX_VALUE - SETTLE_MS) return 0L;
        return confirmedAtMs + SETTLE_MS;
    }

    static boolean blocksTap(long lockUntilMs, long nowMs) {
        if (lockUntilMs < SETTLE_MS || nowMs < 0L) return false;
        long lockStartedAtMs = lockUntilMs - SETTLE_MS;
        return nowMs >= lockStartedAtMs && nowMs < lockUntilMs;
    }

    static boolean isNewConfirmation(String previousRequestId, String requestId) {
        String previous = clean(previousRequestId);
        String current = clean(requestId);
        return !current.isEmpty() && !current.equals(previous);
    }

    private static String clean(String value) {
        String clean = value == null ? "" : value.trim();
        return clean.length() > 160 ? clean.substring(0, 160) : clean;
    }
}
