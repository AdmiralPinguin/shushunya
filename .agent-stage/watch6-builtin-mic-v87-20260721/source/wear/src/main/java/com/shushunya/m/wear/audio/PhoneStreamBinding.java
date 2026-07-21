package com.shushunya.m.wear.audio;

/** Exact phone-owned raw-capture identity associated with the current Watch channel. */
final class PhoneStreamBinding {
    final String phoneNodeId;
    final String requestId;
    final String captureGroupId;
    final long runGeneration;
    final long sessionId;

    PhoneStreamBinding(
            String phoneNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId) {
        this.phoneNodeId = clean(phoneNodeId);
        this.requestId = clean(requestId);
        this.captureGroupId = clean(captureGroupId);
        this.runGeneration = runGeneration;
        this.sessionId = sessionId;
        if (this.phoneNodeId.isEmpty()
                || !WearAudioLifecycleProtocol.validId(this.requestId)
                || !WearAudioLifecycleProtocol.validId(this.captureGroupId)
                || runGeneration <= 0L
                || sessionId <= 0L) {
            throw new IllegalArgumentException("invalid phone stream binding");
        }
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
