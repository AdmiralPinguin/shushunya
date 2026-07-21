package com.shushunya.m.wear.control;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class MagicWakeCoordinatorStateTest {
    @Test
    public void exactCommandRoundTripsAcrossProcessDeath() {
        MagicWakeCoordinatorState original =
                MagicWakeCoordinatorState.discovering("request-123", 100_000L)
                        .withPhoneNode("phone-node", 101_000L)
                        .withNextAttempt(9)
                        .accepted(true);

        MagicWakeCoordinatorState restored =
                MagicWakeCoordinatorState.decode(original.encode());

        assertNotNull(restored);
        assertEquals("request-123", restored.requestId);
        assertEquals(100_000L, restored.issuedAtMs);
        assertEquals("phone-node", restored.phoneNodeId);
        assertEquals(101_000L, restored.wakeStartedAtMs);
        assertEquals(9, restored.nextAttemptIndex);
        assertEquals(
                MagicWakeCoordinatorState.Phase.AWAITING_TERMINAL,
                restored.phase);
        assertTrue(restored.isAccepted());
        assertTrue(restored.targetStart);
        assertTrue(restored.hasPendingAcceptedAction());
    }

    @Test
    public void acceptedStopDirectionAndConsumptionSurviveProcessDeath() {
        MagicWakeCoordinatorState accepted = MagicWakeCoordinatorState.discovering(
                        "request-stop", 300_000L)
                .withPhoneNode("phone-node", 301_000L)
                .accepted(false);
        MagicWakeCoordinatorState pending = MagicWakeCoordinatorState.decode(accepted.encode());
        assertNotNull(pending);
        assertFalse(pending.targetStart);
        assertTrue(pending.hasPendingAcceptedAction());

        MagicWakeCoordinatorState consumed = MagicWakeCoordinatorState.decode(
                pending.acceptedActionConsumed().encode());
        assertNotNull(consumed);
        assertTrue(consumed.acceptedActionConsumed);
        assertFalse(consumed.hasPendingAcceptedAction());
    }

    @Test
    public void discoveringPhasePersistsBeforeNodeExists() {
        MagicWakeCoordinatorState restored = MagicWakeCoordinatorState.decode(
                MagicWakeCoordinatorState.discovering(
                        "request-before-node", 200_000L).encode());
        assertNotNull(restored);
        assertEquals(
                MagicWakeCoordinatorState.Phase.DISCOVERING,
                restored.phase);
        assertEquals("", restored.phoneNodeId);
        assertFalse(restored.isAccepted());
    }

    @Test
    public void malformedPersistenceFailsClosed() {
        assertNull(MagicWakeCoordinatorState.decode(""));
        assertNull(MagicWakeCoordinatorState.decode("2|broken"));
        assertNull(MagicWakeCoordinatorState.decode(
                "1|%%%|1||0|0|DISCOVERING"));
    }
}
