package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

public final class TerminalRetryPolicyTest {
    @Test
    public void firstThreeTerminalMessagesMayDropAndFourthAckCompletes() throws Exception {
        FakeClock clock = new FakeClock(10_000L);
        AtomicInteger sends = new AtomicInteger();
        List<String> payloads = new ArrayList<>();
        String immutablePayload = "terminal-exact-owner";
        String ack = TerminalRetryPolicy.sendUntilAck(
                5_000L,
                clock,
                attempt -> {
                    sends.incrementAndGet();
                    payloads.add(immutablePayload);
                },
                timeout -> {
                    if (sends.get() >= 4) return "exact-ack";
                    clock.sleepMs(timeout);
                    return null;
                });
        assertEquals("exact-ack", ack);
        assertEquals(4, sends.get());
        assertEquals(4, payloads.size());
        for (String payload : payloads) assertEquals(immutablePayload, payload);
        assertEquals(11_500L, clock.nowMs());
    }

    @Test
    public void allFiveDropsEndAtSingleFiveSecondDeadline() throws Exception {
        FakeClock clock = new FakeClock(20_000L);
        AtomicInteger sends = new AtomicInteger();
        String ack = TerminalRetryPolicy.sendUntilAck(
                5_000L,
                clock,
                attempt -> sends.incrementAndGet(),
                timeout -> {
                    clock.sleepMs(timeout);
                    return null;
                });
        assertNull(ack);
        assertEquals(5, sends.get());
        assertEquals(25_000L, clock.nowMs());
    }

    private static final class FakeClock implements TerminalRetryPolicy.Clock {
        private long nowMs;

        FakeClock(long nowMs) {
            this.nowMs = nowMs;
        }

        @Override public long nowMs() {
            return nowMs;
        }

        @Override public void sleepMs(long delayMs) {
            nowMs += Math.max(0L, delayMs);
        }
    }
}
