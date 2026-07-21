package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.After;
import org.junit.Test;

import java.util.Set;

public final class MagicAcceptedPolicyTest {
    @After
    public void clearRegistry() {
        MagicAcceptedRegistry.clearForTest();
    }

    @Test
    public void exactFreshRequestAndSelectedPhoneAreAccepted() {
        assertTrue(MagicAcceptedPolicy.matches(
                "request-1", Set.of("phone-a"),
                "request-1", "phone-a", 1_000L, 2_000L));
    }

    @Test
    public void wrongRequestWrongPhoneAndStaleAckAreRejected() {
        assertFalse(MagicAcceptedPolicy.matches(
                "request-1", Set.of("phone-a"),
                "request-2", "phone-a", 1_000L, 2_000L));
        assertFalse(MagicAcceptedPolicy.matches(
                "request-1", Set.of("phone-a"),
                "request-1", "phone-b", 1_000L, 2_000L));
        assertFalse(MagicAcceptedPolicy.matches(
                "request-1", Set.of("phone-a"),
                "request-1", "phone-a", 1_000L, 36_001L));
    }

    @Test
    public void registryConsumesMatchingAckExactlyOnce() {
        MagicAcceptedRegistry.record("request-1", "phone-a", 1_000L);
        assertEquals("", MagicAcceptedRegistry.consumeMatching(
                "request-1", Set.of("phone-b"), 1_100L));
        assertEquals("phone-a", MagicAcceptedRegistry.consumeMatching(
                "request-1", Set.of("phone-a"), 1_200L));
        assertEquals("", MagicAcceptedRegistry.consumeMatching(
                "request-1", Set.of("phone-a"), 1_300L));
    }

    @Test
    public void registryCarriesTheExactPhoneTargetDirection() {
        MagicAcceptedRegistry.record(
                "request-start", "phone-a", true, 2_000L);
        MagicAcceptedRegistry.AcceptedAck start =
                MagicAcceptedRegistry.consumeMatchingAck(
                        "request-start", Set.of("phone-a"), 2_100L);
        assertEquals("phone-a", start.sourceNodeId);
        assertTrue(start.targetStart);

        MagicAcceptedRegistry.record(
                "request-stop", "phone-a", false, 3_000L);
        MagicAcceptedRegistry.AcceptedAck stop =
                MagicAcceptedRegistry.consumeMatchingAck(
                        "request-stop", Set.of("phone-a"), 3_100L);
        assertEquals("phone-a", stop.sourceNodeId);
        assertFalse(stop.targetStart);
    }
}
