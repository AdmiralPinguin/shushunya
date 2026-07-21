package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.After;
import org.junit.Test;

import java.util.Set;

public final class PreparedAckPolicyTest {
    @After
    public void clearRegistry() {
        PreparedAckRegistry.clearForTest();
    }

    @Test
    public void exactFreshRequestAndSourceAreAccepted() {
        assertTrue(PreparedAckPolicy.matches(
                "request-1", Set.of("phone-a"), "request-1", "phone-a", 1_000L, 2_000L));
    }

    @Test
    public void wrongRequestWrongNodeAndStaleAckAreRejected() {
        assertFalse(PreparedAckPolicy.matches(
                "request-1", Set.of("phone-a"), "request-2", "phone-a", 1_000L, 2_000L));
        assertFalse(PreparedAckPolicy.matches(
                "request-1", Set.of("phone-a"), "request-1", "phone-b", 1_000L, 2_000L));
        assertFalse(PreparedAckPolicy.matches(
                "request-1", Set.of("phone-a"), "request-1", "phone-a", 1_000L, 7_001L));
        assertFalse(PreparedAckPolicy.matches(
                "request-1", Set.of("phone-a"), "request-1", "phone-a", 2_001L, 2_000L));
    }

    @Test
    public void registryKeepsEarlyAckUntilSuccessfulTargetsAreKnown() {
        PreparedAckRegistry.record("request-1", "phone-a", 1_000L);
        assertEquals("", PreparedAckRegistry.consumeMatching(
                "request-1", Set.of("phone-b"), 1_100L));
        assertEquals("phone-a", PreparedAckRegistry.consumeMatching(
                "request-1", Set.of("phone-a"), 1_200L));
        assertEquals("", PreparedAckRegistry.consumeMatching(
                "request-1", Set.of("phone-a"), 1_300L));
    }

    @Test
    public void staleCachedAckCannotAuthorizeLaterDispatch() {
        PreparedAckRegistry.record("request-1", "phone-a", 1_000L);
        assertEquals("", PreparedAckRegistry.consumeMatching(
                "request-1", Set.of("phone-a"), 7_001L));
    }
}
