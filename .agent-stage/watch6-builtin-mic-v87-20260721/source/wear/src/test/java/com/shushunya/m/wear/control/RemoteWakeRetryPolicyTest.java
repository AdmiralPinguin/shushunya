package com.shushunya.m.wear.control;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class RemoteWakeRetryPolicyTest {
    @Test
    public void retriesAreBoundedAndUseOneRequestWindow() {
        assertArrayEquals(
                new long[] {
                        0L, 800L, 1_800L, 3_500L, 6_000L, 9_500L, 14_000L,
                        19_000L, 24_000L, 31_000L, 39_000L, 48_000L, 57_000L
                },
                RemoteWakeRetryPolicy.delaysMs());
        assertTrue(RemoteWakeRetryPolicy.shouldAttempt(false, false, true, 0));
        assertTrue(RemoteWakeRetryPolicy.shouldAttempt(false, false, true, 12));
        assertFalse(RemoteWakeRetryPolicy.shouldAttempt(false, false, true, 13));
    }

    @Test
    public void acceptedDoesNotSuppressRetryUntilExactTerminalClearsPending() {
        assertTrue(RemoteWakeRetryPolicy.shouldAttempt(true, false, true, 9));
        for (int attempt = 0; attempt < 13; attempt++) {
            assertFalse(RemoteWakeRetryPolicy.shouldAttempt(
                    true, false, false, attempt));
        }
    }

    @Test
    public void preAcceptTimeoutStillSuppressesRecovery() {
        assertTrue(RemoteWakeRetryPolicy.shouldAttempt(
                false, false, true, 8));
        assertFalse(RemoteWakeRetryPolicy.shouldAttempt(
                false, true, true, 8));
    }

    @Test
    public void lateDiscoveryUsesEarlierGlobalPhoneFreshnessDeadline() {
        long issuedAt = 100_000L;
        long wakeStartedAt = 124_000L;
        assertEquals(
                130_000L,
                RemoteWakeRetryPolicy.preAcceptDeadlineMs(
                        issuedAt, wakeStartedAt));
        assertFalse(RemoteWakeRetryPolicy.preAcceptTimedOut(
                false, issuedAt, wakeStartedAt, 129_999L));
        assertTrue(RemoteWakeRetryPolicy.preAcceptTimedOut(
                false, issuedAt, wakeStartedAt, 130_000L));
        // 6 s after wake lands exactly on the phone's freshness edge and is
        // not scheduled; once ACCEPTED is durable, the same retry is allowed.
        assertFalse(RemoteWakeRetryPolicy.mayScheduleAttempt(
                false, issuedAt, wakeStartedAt, 4));
        assertTrue(RemoteWakeRetryPolicy.mayScheduleAttempt(
                true, issuedAt, wakeStartedAt, 12));
    }

    @Test
    public void processResumePerformsOneCatchUpThenSkipsPastSchedulePoints() {
        assertEquals(8, RemoteWakeRetryPolicy.nextIndexAfterAttempt(2, 20_000L));
        assertEquals(13, RemoteWakeRetryPolicy.nextIndexAfterAttempt(8, 58_000L));
    }

    @Test
    public void remoteFutureSuccessIsNotSemanticAcceptance() {
        assertFalse(RemoteWakeRetryPolicy.isSemanticAcceptance(true, false));
        assertFalse(RemoteWakeRetryPolicy.isSemanticAcceptance(false, false));
        assertTrue(RemoteWakeRetryPolicy.isSemanticAcceptance(false, true));
        assertTrue(RemoteWakeRetryPolicy.isSemanticAcceptance(true, true));
    }

    @Test
    public void callerCannotMutateSharedRetrySchedule() {
        long[] schedule = RemoteWakeRetryPolicy.delaysMs();
        schedule[0] = 99L;
        assertArrayEquals(
                new long[] {
                        0L, 800L, 1_800L, 3_500L, 6_000L, 9_500L, 14_000L,
                        19_000L, 24_000L, 31_000L, 39_000L, 48_000L, 57_000L
                },
                RemoteWakeRetryPolicy.delaysMs());
    }
}
