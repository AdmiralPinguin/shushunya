package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ChannelReconnectPolicyTest {
    @Test
    public void atMostThreeAttemptsFitInsidePhoneRecoveryWindow() {
        long firstFailure = 10_000L;
        long deadline = ChannelReconnectPolicy.deadline(firstFailure);
        assertEquals(firstFailure + 7_000L, deadline);
        assertTrue(ChannelReconnectPolicy.mayAttempt(0, firstFailure, deadline));
        assertTrue(ChannelReconnectPolicy.mayAttempt(2, deadline - 1L, deadline));
        assertFalse(ChannelReconnectPolicy.mayAttempt(3, firstFailure, deadline));
        assertFalse(ChannelReconnectPolicy.mayAttempt(0, deadline, deadline));
    }

    @Test
    public void successfulReplacementDoesNotCreateANewBudgetAfterFlap() {
        long firstFailure = 20_000L;
        long immutableDeadline = ChannelReconnectPolicy.deadline(firstFailure);
        // Attempt 0 succeeds quickly. A later fault still uses attempt 1 and the
        // original deadline, rather than resetting either value.
        assertTrue(ChannelReconnectPolicy.mayAttempt(
                1, immutableDeadline - 1L, immutableDeadline));
        assertFalse(ChannelReconnectPolicy.mayAttempt(
                1, immutableDeadline, immutableDeadline));
        assertEquals(0L, ChannelReconnectPolicy.taskTimeoutMs(
                immutableDeadline, immutableDeadline));
    }

    @Test
    public void perAttemptTaskTimeoutIsClampedToCumulativeDeadline() {
        assertEquals(1_000L, ChannelReconnectPolicy.taskTimeoutMs(1_000L, 5_000L));
        assertEquals(125L, ChannelReconnectPolicy.taskTimeoutMs(4_875L, 5_000L));
    }
}
