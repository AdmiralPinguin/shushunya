package com.shushunya.m.wear.control;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class AcceptedActionReplayPolicyTest {
    @Test
    public void onlyUnconsumedExactPendingAcceptanceReplays() {
        assertTrue(AcceptedActionReplayPolicy.shouldReplay(true, false, true));
        assertFalse(AcceptedActionReplayPolicy.shouldReplay(false, false, true));
        assertFalse(AcceptedActionReplayPolicy.shouldReplay(true, true, true));
        assertFalse(AcceptedActionReplayPolicy.shouldReplay(true, false, false));
    }
}
