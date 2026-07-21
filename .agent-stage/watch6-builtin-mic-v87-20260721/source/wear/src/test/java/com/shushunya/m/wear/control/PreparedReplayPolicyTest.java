package com.shushunya.m.wear.control;

import static org.junit.Assert.assertArrayEquals;

import org.junit.Test;

public final class PreparedReplayPolicyTest {
    @Test
    public void oneTapReplaysOnlyTheSamePreparedCapability() {
        assertArrayEquals(
                new long[] {0L, 250L, 750L, 1_500L, 3_000L, 5_000L, 8_000L, 12_000L},
                PreparedReplayPolicy.delaysMs());
    }

    @Test
    public void callerCannotMutateTheSharedSchedule() {
        long[] first = PreparedReplayPolicy.delaysMs();
        first[0] = 99L;
        assertArrayEquals(
                new long[] {0L, 250L, 750L, 1_500L, 3_000L, 5_000L, 8_000L, 12_000L},
                PreparedReplayPolicy.delaysMs());
    }
}
