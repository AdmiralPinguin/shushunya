package com.shushunya.m.wear.audio;

/** Immutable first-wins owner of one exact phone STOP/drain transaction. */
final class DrainRequestOwner {
    enum Decision { ACCEPT_NEW, ACCEPT_DUPLICATE, REJECT }

    final String phoneNodeId;
    final String requestId;
    final String captureGroupId;
    final long runGeneration;
    final long requestedSessionId;
    final long timeoutMs;

    private DrainRequestOwner(
            String phoneNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long requestedSessionId,
            long timeoutMs) {
        this.phoneNodeId = clean(phoneNodeId);
        this.requestId = clean(requestId);
        this.captureGroupId = clean(captureGroupId);
        this.runGeneration = runGeneration;
        this.requestedSessionId = requestedSessionId;
        this.timeoutMs = timeoutMs;
    }

    static Decision decide(
            DrainRequestOwner existing,
            PhoneStreamBinding binding,
            String sourceNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            long timeoutMs) {
        if (binding == null
                || !WearAudioLifecycleProtocol.validId(requestId)
                || timeoutMs <= 0L
                || timeoutMs > WearAudioLifecycleProtocol.MAX_DRAIN_TIMEOUT_MS
                || binding.requestId.equals(clean(requestId))
                || !binding.phoneNodeId.equals(clean(sourceNodeId))
                || !binding.captureGroupId.equals(clean(captureGroupId))
                || binding.runGeneration != runGeneration
                || (sessionId != 0L && binding.sessionId != sessionId)) {
            return Decision.REJECT;
        }
        if (existing == null) return Decision.ACCEPT_NEW;
        // The STOP UUID/group/run owner is immutable, while the exact session
        // may legitimately rebase after a channel replacement. Accept only the
        // session currently proven by the newest phone binding (or wildcard 0),
        // never the old session after replacement.
        return existing.phoneNodeId.equals(clean(sourceNodeId))
                && existing.requestId.equals(clean(requestId))
                && existing.captureGroupId.equals(clean(captureGroupId))
                && existing.runGeneration == runGeneration
                && (sessionId == 0L || binding.sessionId == sessionId)
                && existing.timeoutMs == timeoutMs
                ? Decision.ACCEPT_DUPLICATE
                : Decision.REJECT;
    }

    static DrainRequestOwner create(
            PhoneStreamBinding binding,
            String requestId,
            long requestedSessionId,
            long timeoutMs) {
        if (binding == null
                || !WearAudioLifecycleProtocol.validId(requestId)
                || binding.requestId.equals(clean(requestId))
                || requestedSessionId < 0L
                || (requestedSessionId != 0L
                && requestedSessionId != binding.sessionId)
                || timeoutMs <= 0L
                || timeoutMs > WearAudioLifecycleProtocol.MAX_DRAIN_TIMEOUT_MS) {
            throw new IllegalArgumentException("invalid drain owner");
        }
        return new DrainRequestOwner(
                binding.phoneNodeId,
                requestId,
                binding.captureGroupId,
                binding.runGeneration,
                requestedSessionId,
                timeoutMs);
    }

    boolean matchesAck(
            PhoneStreamBinding terminalBinding,
            String sourceNodeId,
            String ackRequestId,
            String ackCaptureGroupId,
            long ackRunGeneration,
            long ackSessionId) {
        return terminalBinding != null
                && phoneNodeId.equals(clean(sourceNodeId))
                && terminalBinding.phoneNodeId.equals(phoneNodeId)
                && requestId.equals(clean(ackRequestId))
                && captureGroupId.equals(clean(ackCaptureGroupId))
                && runGeneration == ackRunGeneration
                && terminalBinding.captureGroupId.equals(captureGroupId)
                && terminalBinding.runGeneration == runGeneration
                && terminalBinding.sessionId == ackSessionId;
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
