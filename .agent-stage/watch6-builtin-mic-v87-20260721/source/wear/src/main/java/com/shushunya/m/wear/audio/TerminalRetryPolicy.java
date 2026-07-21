package com.shushunya.m.wear.audio;

/** Fixed resend schedule inside the Watch's single five-second ACK deadline. */
final class TerminalRetryPolicy {
    private static final long[] OFFSETS_MS = {0L, 250L, 750L, 1_500L, 3_000L};

    private TerminalRetryPolicy() {}

    interface Clock {
        long nowMs();
        void sleepMs(long delayMs) throws InterruptedException;
    }

    interface Sender {
        void send(int attemptIndex);
    }

    interface AckWaiter<T> {
        T await(long timeoutMs) throws InterruptedException;
    }

    static int attemptCount() {
        return OFFSETS_MS.length;
    }

    static long offsetMs(int attemptIndex) {
        if (attemptIndex < 0 || attemptIndex >= OFFSETS_MS.length) return -1L;
        return OFFSETS_MS[attemptIndex];
    }

    /** Runs every retry inside one non-extending deadline. */
    static <T> T sendUntilAck(
            long timeoutMs,
            Clock clock,
            Sender sender,
            AckWaiter<T> waiter) throws InterruptedException {
        if (timeoutMs <= 0L || clock == null || sender == null || waiter == null) return null;
        long startedAtMs = clock.nowMs();
        long deadlineMs = safeAdd(startedAtMs, timeoutMs);
        for (int attempt = 0; attempt < OFFSETS_MS.length; attempt++) {
            long dueAtMs = safeAdd(startedAtMs, OFFSETS_MS[attempt]);
            long sleepMs = dueAtMs - clock.nowMs();
            if (sleepMs > 0L) clock.sleepMs(sleepMs);
            if (clock.nowMs() >= deadlineMs) break;
            sender.send(attempt);
            long nextDueAtMs = attempt + 1 < OFFSETS_MS.length
                    ? safeAdd(startedAtMs, OFFSETS_MS[attempt + 1]) : deadlineMs;
            long waitMs = Math.min(nextDueAtMs, deadlineMs) - clock.nowMs();
            T ack = waiter.await(Math.max(0L, waitMs));
            if (ack != null) return ack;
        }
        return waiter.await(Math.max(0L, deadlineMs - clock.nowMs()));
    }

    private static long safeAdd(long first, long second) {
        return first > Long.MAX_VALUE - second ? Long.MAX_VALUE : first + second;
    }
}
