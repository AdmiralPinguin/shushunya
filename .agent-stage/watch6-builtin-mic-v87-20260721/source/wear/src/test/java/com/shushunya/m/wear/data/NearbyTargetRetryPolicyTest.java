package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class NearbyTargetRetryPolicyTest {
    @Test
    public void coldDiscoveryRetriesAreBoundedAndBackOff() {
        assertEquals(250L, NearbyTargetRetryPolicy.delayAfterFailure(0));
        assertEquals(500L, NearbyTargetRetryPolicy.delayAfterFailure(1));
        assertEquals(750L, NearbyTargetRetryPolicy.delayAfterFailure(2));
        assertEquals(1_500L, NearbyTargetRetryPolicy.delayAfterFailure(3));
        assertEquals(3_000L, NearbyTargetRetryPolicy.delayAfterFailure(4));
        assertEquals(-1L, NearbyTargetRetryPolicy.delayAfterFailure(5));
        assertEquals(-1L, NearbyTargetRetryPolicy.delayAfterFailure(-1));
    }
}
