package com.shushunya.m.wear.control;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class VisibleMicLaunchPolicyTest {
    @Test
    public void startRequiresStillResumedActivity() {
        assertEquals(
                VisibleMicLaunchPolicy.Action.START,
                VisibleMicLaunchPolicy.decide(true, true, false, false));
        assertEquals(
                VisibleMicLaunchPolicy.Action.DEFER,
                VisibleMicLaunchPolicy.decide(true, false, false, false));
        assertEquals(
                VisibleMicLaunchPolicy.Action.DEFER,
                VisibleMicLaunchPolicy.decide(true, true, true, false));
        assertEquals(
                VisibleMicLaunchPolicy.Action.DEFER,
                VisibleMicLaunchPolicy.decide(true, true, false, true));
    }

    @Test
    public void stopIntentWaitsForSeparatelyCorrelatedPhoneDrain() {
        assertEquals(
                VisibleMicLaunchPolicy.Action.WAIT_FOR_PHONE_DRAIN,
                VisibleMicLaunchPolicy.decide(false, false, true, true));
    }
}
