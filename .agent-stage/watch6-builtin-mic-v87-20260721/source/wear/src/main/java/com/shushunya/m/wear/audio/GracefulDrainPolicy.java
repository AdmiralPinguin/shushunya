package com.shushunya.m.wear.audio;

/** Pure decision surface shared by the producer/writer shutdown loop and tests. */
final class GracefulDrainPolicy {
    private GracefulDrainPolicy() {}

    static boolean shouldCapture(
            boolean generationOwned,
            boolean gracefulStopRequested,
            boolean hardAbortRequested) {
        return generationOwned && !gracefulStopRequested && !hardAbortRequested;
    }

    static boolean shouldWriterContinue(
            boolean generationOwned,
            boolean hardAbortRequested,
            boolean producerRunning,
            boolean queueEmpty) {
        return generationOwned
                && !hardAbortRequested
                && (producerRunning || !queueEmpty);
    }

    static boolean drainExpired(
            boolean gracefulStopRequested,
            long drainDeadlineElapsedMs,
            long nowElapsedMs,
            boolean producerRunning,
            boolean queueEmpty) {
        return gracefulStopRequested
                && (producerRunning || !queueEmpty)
                && drainDeadlineElapsedMs > 0L
                && nowElapsedMs > drainDeadlineElapsedMs;
    }

    static String terminalDisposition(
            boolean gracefulStopRequested,
            long lastSequence,
            long droppedFrames,
            boolean streamFailed) {
        return gracefulStopRequested
                && !streamFailed
                && lastSequence >= -1L
                && droppedFrames == 0L
                ? WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS
                : WearAudioLifecycleProtocol.DISPOSITION_HARD_FAILURE;
    }
}
