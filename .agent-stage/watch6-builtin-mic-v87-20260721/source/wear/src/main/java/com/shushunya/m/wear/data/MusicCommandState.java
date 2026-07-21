package com.shushunya.m.wear.data;

import java.nio.charset.StandardCharsets;

/** Immutable durable envelope; every retry reuses {@link #jsonPayload}. */
final class MusicCommandState {
    final String requestId;
    final long issuedAtMs;
    final String jsonPayload;
    final long startedAtMs;
    final long deadlineAtMs;
    final String phoneNodeId;
    final int nextAttemptIndex;

    private MusicCommandState(
            String requestId,
            long issuedAtMs,
            String jsonPayload,
            long startedAtMs,
            long deadlineAtMs,
            String phoneNodeId,
            int nextAttemptIndex) {
        this.requestId = clean(requestId);
        this.issuedAtMs = issuedAtMs;
        this.jsonPayload = jsonPayload == null ? "" : jsonPayload;
        this.startedAtMs = startedAtMs;
        this.deadlineAtMs = deadlineAtMs;
        this.phoneNodeId = cleanNode(phoneNodeId);
        this.nextAttemptIndex = nextAttemptIndex;
    }

    static MusicCommandState create(WearProtocol.Request request, long startedAtMs) {
        if (request == null) return null;
        return restore(
                request.id,
                request.issuedAtMs,
                request.json,
                startedAtMs,
                MusicRetryPolicy.deadlineAt(startedAtMs),
                "",
                0);
    }

    static MusicCommandState restore(
            String requestId,
            long issuedAtMs,
            String jsonPayload,
            long startedAtMs,
            long deadlineAtMs,
            String phoneNodeId,
            int nextAttemptIndex) {
        String rawNode = phoneNodeId == null ? "" : phoneNodeId.trim();
        if (!rawNode.isEmpty() && rawNode.length() > 256) return null;
        MusicCommandState state = new MusicCommandState(
                requestId,
                issuedAtMs,
                jsonPayload,
                startedAtMs,
                deadlineAtMs,
                phoneNodeId,
                nextAttemptIndex);
        return state.isValid() ? state : null;
    }

    MusicCommandState withPhoneNode(String nodeId) {
        return restore(
                requestId,
                issuedAtMs,
                jsonPayload,
                startedAtMs,
                deadlineAtMs,
                nodeId,
                nextAttemptIndex);
    }

    MusicCommandState withNextAttempt(int nextIndex) {
        return restore(
                requestId,
                issuedAtMs,
                jsonPayload,
                startedAtMs,
                deadlineAtMs,
                phoneNodeId,
                nextIndex);
    }

    boolean isValid() {
        if (!requestId.matches("[A-Za-z0-9._:-]{1,256}")
                || issuedAtMs <= 0L
                || startedAtMs <= 0L
                || deadlineAtMs != MusicRetryPolicy.deadlineAt(startedAtMs)
                || nextAttemptIndex < 0
                || nextAttemptIndex > MusicRetryPolicy.attemptCount()) return false;
        byte[] payload = jsonPayload.getBytes(StandardCharsets.UTF_8);
        return payload.length > 0
                && payload.length <= 16_384
                && jsonPayload.equals(WearProtocol.requestJson(requestId, issuedAtMs));
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static String cleanNode(String value) {
        String clean = clean(value);
        return clean.length() <= 256 ? clean : "";
    }
}
