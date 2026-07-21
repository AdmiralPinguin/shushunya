package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayDeque;

public final class GracefulDrainPolicyTest {
    @Test
    public void allTwelveAlreadyCapturedFramesDrainInOrderAfterProducerStops() {
        ArrayDeque<Integer> queue = new ArrayDeque<>();
        for (int sequence = 0; sequence < 12; sequence++) queue.addLast(sequence);
        int expected = 0;
        while (GracefulDrainPolicy.shouldWriterContinue(
                true, false, false, queue.isEmpty())) {
            assertEquals(expected++, (int) queue.removeFirst());
        }
        assertEquals(12, expected);
        assertTrue(queue.isEmpty());
    }

    @Test
    public void gracefulStopHaltsProducerButKeepsWriterUntilQueueEmpty() {
        assertFalse(GracefulDrainPolicy.shouldCapture(true, true, false));
        assertTrue(GracefulDrainPolicy.shouldWriterContinue(
                true, false, false, false));
        assertFalse(GracefulDrainPolicy.shouldWriterContinue(
                true, false, false, true));
    }

    @Test
    public void zeroFrameGracefulSessionIsValid() {
        assertEquals(
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS,
                GracefulDrainPolicy.terminalDisposition(true, -1L, 0L, false));
    }

    @Test
    public void anyMeasuredDropOrFailureMakesTerminalHard() {
        assertEquals(
                WearAudioLifecycleProtocol.DISPOSITION_HARD_FAILURE,
                GracefulDrainPolicy.terminalDisposition(true, 10L, 1L, false));
        assertEquals(
                WearAudioLifecycleProtocol.DISPOSITION_HARD_FAILURE,
                GracefulDrainPolicy.terminalDisposition(true, 10L, 0L, true));
    }

    @Test
    public void boundedDeadlineFailsOnlyWhileWorkRemains() {
        assertTrue(GracefulDrainPolicy.drainExpired(
                true, 100L, 101L, false, false));
        assertFalse(GracefulDrainPolicy.drainExpired(
                true, 100L, 101L, false, true));
    }
}
