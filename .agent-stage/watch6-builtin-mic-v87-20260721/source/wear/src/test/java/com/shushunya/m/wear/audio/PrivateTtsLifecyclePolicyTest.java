package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class PrivateTtsLifecyclePolicyTest {
    @Test
    public void runningAndUnknownKeepSinkArmed() {
        assertFalse(PrivateTtsLifecyclePolicy.shouldStop("running", false));
        assertFalse(PrivateTtsLifecyclePolicy.shouldStop("", false));
    }

    @Test
    public void everyTerminalStateStopsSink() {
        assertTrue(PrivateTtsLifecyclePolicy.shouldStop("paused", false));
        assertTrue(PrivateTtsLifecyclePolicy.shouldStop("stopped", false));
        assertTrue(PrivateTtsLifecyclePolicy.shouldStop("error", false));
        assertTrue(PrivateTtsLifecyclePolicy.shouldStop("failed", false));
        assertTrue(PrivateTtsLifecyclePolicy.shouldStop("running", true));
    }
}
