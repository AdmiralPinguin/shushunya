package com.shushunya.m.wear.audio;

import java.util.Locale;

/** Pure mapping from confirmed phone live state to private sink lifetime. */
public final class PrivateTtsLifecyclePolicy {
    private PrivateTtsLifecyclePolicy() {}

    public static boolean shouldStop(String liveState, boolean hasError) {
        if (hasError) return true;
        String clean = liveState == null
                ? ""
                : liveState.trim().toLowerCase(Locale.ROOT);
        return "stopped".equals(clean)
                || "paused".equals(clean)
                || "error".equals(clean)
                || "failed".equals(clean);
    }
}
