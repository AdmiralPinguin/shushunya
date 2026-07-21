package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class MusicRetryPolicyTest {
    @Test
    public void retryScheduleIsBoundedAndFrontLoaded() {
        long previous = -1L;
        assertEquals(7, MusicRetryPolicy.attemptCount());
        for (int index = 0; index < MusicRetryPolicy.attemptCount(); index++) {
            long offset = MusicRetryPolicy.offsetMs(index);
            assertTrue(offset > previous);
            assertTrue(offset < MusicRetryPolicy.BUDGET_MS);
            previous = offset;
        }
        assertEquals(-1L, MusicRetryPolicy.offsetMs(-1));
        assertEquals(-1L, MusicRetryPolicy.offsetMs(7));
    }

    @Test
    public void processReplayUsesOriginalDeadlineNotANewBudget() {
        long started = 10_000L;
        long deadline = MusicRetryPolicy.deadlineAt(started);
        assertTrue(MusicRetryPolicy.isAlive(started, deadline, started));
        assertTrue(MusicRetryPolicy.isAlive(started, deadline, deadline - 1L));
        assertFalse(MusicRetryPolicy.isAlive(started, deadline, deadline));
        assertFalse(MusicRetryPolicy.isAlive(started, deadline, started - 1L));
        assertFalse(MusicRetryPolicy.isAlive(started, deadline + 1L, started));
    }

    @Test
    public void overdueAttemptRunsImmediatelyButNeverBeforeOriginalStart() {
        assertEquals(0L, MusicRetryPolicy.delayUntilAttempt(
                1_000L, 2, 2_000L));
        assertEquals(250L, MusicRetryPolicy.delayUntilAttempt(
                1_000L, 1, 1_000L));
        assertEquals(-1L, MusicRetryPolicy.delayUntilAttempt(
                1_000L, 0, 999L));
    }
}
