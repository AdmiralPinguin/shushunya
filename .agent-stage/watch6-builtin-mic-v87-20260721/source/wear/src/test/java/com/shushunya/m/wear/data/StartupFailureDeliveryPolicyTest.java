package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class StartupFailureDeliveryPolicyTest {
    @Test
    public void transportQueueSuccessCannotCompleteDeliveryWithoutApplicationAck() {
        // There is intentionally no MessageClient/transport-success input.
        assertTrue(StartupFailureDeliveryPolicy.shouldRetry(1, false));
        assertTrue(StartupFailureDeliveryPolicy.shouldRetry(6, false));
        assertFalse(StartupFailureDeliveryPolicy.shouldRetry(7, false));
        assertFalse(StartupFailureDeliveryPolicy.shouldRetry(1, true));
    }

    @Test
    public void persistedRetryBackoffExhaustsAfterOneBoundedBurst() {
        assertEquals(0L, StartupFailureDeliveryPolicy.delayForAttempt(0));
        assertEquals(12_000L, StartupFailureDeliveryPolicy.delayForAttempt(6));
        assertEquals(-1L, StartupFailureDeliveryPolicy.delayForAttempt(7));
        assertEquals(7, StartupFailureDeliveryPolicy.nextAttempt(6));
        assertEquals(7, StartupFailureDeliveryPolicy.nextAttempt(Integer.MAX_VALUE));
        assertTrue(StartupFailureDeliveryPolicy.isExhausted(7));
    }

    @Test
    public void ackMustMatchExactPinnedNodeRequestCodeAndFailureEpoch() {
        assertTrue(StartupFailureDeliveryPolicy.sameDelivery(
                "phone", "request", "POWERCONF_ZERO_PCM", 123L,
                "phone", "request", "POWERCONF_ZERO_PCM", 123L));
        assertFalse(StartupFailureDeliveryPolicy.sameDelivery(
                "phone", "request", "POWERCONF_ZERO_PCM", 123L,
                "other", "request", "POWERCONF_ZERO_PCM", 123L));
        assertFalse(StartupFailureDeliveryPolicy.sameDelivery(
                "phone", "request", "POWERCONF_ZERO_PCM", 123L,
                "phone", "request", "POWERCONF_ZERO_PCM", 124L));
    }
}
