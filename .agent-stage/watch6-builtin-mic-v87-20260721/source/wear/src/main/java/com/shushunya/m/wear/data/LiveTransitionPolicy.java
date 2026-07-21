package com.shushunya.m.wear.data;

/**
 * Pure transition policy for the live-translator complication.
 *
 * <p>The watch face treats {@code STARTING}/{@code STOPPING} as machine tokens,
 * so keep those enum names stable.</p>
 */
final class LiveTransitionPolicy {
    static final long MIN_VISIBLE_MS = 750L;

    private LiveTransitionPolicy() {}

    static ControllerStateStore.LivePhase directionFor(
            ControllerStateStore.State confirmedState) {
        return isTranslatorVisibleAsOn(confirmedState)
                ? ControllerStateStore.LivePhase.STOPPING
                : ControllerStateStore.LivePhase.STARTING;
    }

    static boolean isTranslatorVisibleAsOn(ControllerStateStore.State state) {
        return state == ControllerStateStore.State.RUNNING;
    }

    static boolean sourceIconIsChaos(
            ControllerStateStore.LivePhase phase,
            ControllerStateStore.State confirmedState) {
        if (phase == ControllerStateStore.LivePhase.STARTING) return false;
        if (phase == ControllerStateStore.LivePhase.STOPPING) return true;
        return isTranslatorVisibleAsOn(confirmedState);
    }

    static long clearDelayMs(
            ControllerStateStore.LivePhase phase,
            boolean confirmed,
            long startedAt,
            long now) {
        if (phase == ControllerStateStore.LivePhase.NONE
                || !confirmed
                || startedAt <= 0L) return -1L;
        return Math.max(0L, MIN_VISIBLE_MS - Math.max(0L, now - startedAt));
    }

    static boolean canClear(
            ControllerStateStore.LivePhase phase,
            boolean confirmed,
            long startedAt,
            long now) {
        return clearDelayMs(phase, confirmed, startedAt, now) == 0L;
    }

    static boolean acceptsTap(
            long lastTapAt,
            long pendingAt,
            long now,
            long pendingTimeoutMs,
            long debounceMs) {
        if (pendingAt > 0L && now - pendingAt <= pendingTimeoutMs) return false;
        return now - lastTapAt >= debounceMs;
    }
}
