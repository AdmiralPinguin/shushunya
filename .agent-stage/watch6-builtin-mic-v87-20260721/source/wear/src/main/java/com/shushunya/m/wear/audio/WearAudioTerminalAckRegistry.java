package com.shushunya.m.wear.audio;

/** One-process bounded mailbox for the exact terminal ACK. */
final class WearAudioTerminalAckRegistry {
    private static Ack pending;
    private static boolean armed;
    private static String expectedDisposition = "";
    private static long expectedLastSequence = Long.MIN_VALUE;

    private WearAudioTerminalAckRegistry() {}

    static synchronized void clear() {
        pending = null;
        armed = false;
        expectedDisposition = "";
        expectedLastSequence = Long.MIN_VALUE;
    }

    static synchronized boolean expect(String disposition, long lastSequence) {
        String expectedAck;
        if (WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS.equals(disposition)) {
            expectedAck = WearAudioLifecycleProtocol.ACK_FINISHED;
        } else if (WearAudioLifecycleProtocol.DISPOSITION_HARD_FAILURE.equals(disposition)) {
            expectedAck = WearAudioLifecycleProtocol.ACK_ABORTED;
        } else {
            return false;
        }
        if (lastSequence < -1L
                || lastSequence > WearAudioLifecycleProtocol.MAX_UNSIGNED_SEQUENCE) {
            return false;
        }
        pending = null;
        expectedDisposition = expectedAck;
        expectedLastSequence = lastSequence;
        armed = true;
        return true;
    }

    static synchronized boolean record(
            PhoneStreamBinding binding,
            DrainRequestOwner drainOwner,
            String sourceNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            String disposition,
            long acceptedLastSequence,
            String error,
            long ackAtMs) {
        boolean identityMatches = binding != null
                && (drainOwner != null
                ? drainOwner.matchesAck(
                        binding,
                        sourceNodeId,
                        requestId,
                        captureGroupId,
                        runGeneration,
                        sessionId)
                : binding.phoneNodeId.equals(clean(sourceNodeId))
                        && binding.requestId.equals(clean(requestId))
                        && binding.captureGroupId.equals(clean(captureGroupId))
                        && binding.runGeneration == runGeneration
                        && binding.sessionId == sessionId);
        boolean finished = WearAudioLifecycleProtocol.ACK_FINISHED.equals(disposition);
        boolean aborted = WearAudioLifecycleProtocol.ACK_ABORTED.equals(disposition);
        String cleanError = clean(error);
        if (!armed
                || !identityMatches
                || (!finished && !aborted)
                || acceptedLastSequence < -1L
                || acceptedLastSequence > WearAudioLifecycleProtocol.MAX_UNSIGNED_SEQUENCE
                || (error != null
                && error.length() > WearAudioLifecycleProtocol.MAX_DETAIL_CHARS)
                || ackAtMs <= 0L
                // Success is the only path allowed to prove lossless FINISH.
                || (finished && (!expectedDisposition.equals(disposition)
                || acceptedLastSequence != expectedLastSequence
                || !cleanError.isEmpty()))
                // A byte-exact negative receipt from the same STOP/session is
                // terminal too. It may report an earlier accepted sequence
                // after phone crash recovery, but must carry a real reason.
                || (aborted && cleanError.isEmpty())) return false;
        Ack candidate = new Ack(
                disposition,
                acceptedLastSequence,
                cleanError,
                ackAtMs);
        if (pending != null) return pending.sameAs(candidate);
        pending = candidate;
        WearAudioTerminalAckRegistry.class.notifyAll();
        return true;
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    static synchronized Ack await(long timeoutMs) throws InterruptedException {
        if (timeoutMs <= 0L) return pending;
        long timeoutNanos = timeoutMs > Long.MAX_VALUE / 1_000_000L
                ? Long.MAX_VALUE : timeoutMs * 1_000_000L;
        long now = System.nanoTime();
        long deadline = now > Long.MAX_VALUE - timeoutNanos
                ? Long.MAX_VALUE : now + timeoutNanos;
        while (pending == null) {
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0L) break;
            long waitMillis = Math.max(1L, remaining / 1_000_000L);
            WearAudioTerminalAckRegistry.class.wait(waitMillis);
        }
        return pending;
    }

    static final class Ack {
        final String disposition;
        final long acceptedLastSequence;
        final String error;
        final long ackAtMs;

        Ack(String disposition, long acceptedLastSequence, String error, long ackAtMs) {
            this.disposition = disposition;
            this.acceptedLastSequence = acceptedLastSequence;
            this.error = error;
            this.ackAtMs = ackAtMs;
        }

        boolean confirmsGraceful(long expectedLastSequence) {
            return WearAudioLifecycleProtocol.ACK_FINISHED.equals(disposition)
                    && error.isEmpty()
                    && acceptedLastSequence == expectedLastSequence;
        }

        private boolean sameAs(Ack other) {
            return other != null
                    && disposition.equals(other.disposition)
                    && acceptedLastSequence == other.acceptedLastSequence
                    && error.equals(other.error)
                    && ackAtMs == other.ackAtMs;
        }
    }
}
