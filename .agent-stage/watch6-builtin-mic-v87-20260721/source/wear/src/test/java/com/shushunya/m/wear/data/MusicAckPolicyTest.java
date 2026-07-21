package com.shushunya.m.wear.data;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class MusicAckPolicyTest {
    @Test
    public void exactCorrelatedPhoneStateIsTerminal() {
        for (String state : new String[] {"playing", "paused", "unavailable"}) {
            assertTrue(MusicAckPolicy.accepts(
                    "uuid-1", "phone-1", "uuid-1", "phone-1", state, true));
        }
    }

    @Test
    public void backgroundWrongPeerOrWrongUuidCannotStopRetries() {
        assertFalse(MusicAckPolicy.accepts(
                "uuid-1", "phone-1", "", "phone-1", "paused", true));
        assertFalse(MusicAckPolicy.accepts(
                "uuid-1", "phone-1", "uuid-2", "phone-1", "paused", true));
        assertFalse(MusicAckPolicy.accepts(
                "uuid-1", "phone-1", "uuid-1", "phone-2", "paused", true));
        assertFalse(MusicAckPolicy.accepts(
                "uuid-1", "phone-1", " uuid-1 ", "phone-1", "paused", true));
    }

    @Test
    public void malformedOrNonSemanticStateCannotStopRetries() {
        assertFalse(MusicAckPolicy.accepts(
                "uuid-1", "phone-1", "uuid-1", "phone-1", "", true));
        assertFalse(MusicAckPolicy.accepts(
                "uuid-1", "phone-1", "uuid-1", "phone-1", "buffering", true));
        assertFalse(MusicAckPolicy.accepts(
                "uuid-1", "phone-1", "uuid-1", "phone-1", "paused", false));
    }
}
