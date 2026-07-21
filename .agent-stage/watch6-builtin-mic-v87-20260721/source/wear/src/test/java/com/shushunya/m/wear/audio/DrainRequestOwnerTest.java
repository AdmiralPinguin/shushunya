package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class DrainRequestOwnerTest {
    private static PhoneStreamBinding binding(long sessionId) {
        return new PhoneStreamBinding(
                "phone-node", "bind-capture-456", "capture-456", 7L, sessionId);
    }

    @Test
    public void drainOwnerMustDifferFromStartBindingUuid() {
        assertEquals(
                DrainRequestOwner.Decision.REJECT,
                DrainRequestOwner.decide(
                        null, binding(900L), "phone-node", "bind-capture-456",
                        "capture-456", 7L, 900L, 8_000L));
    }

    @Test
    public void drainBeforeBindingIsIgnoredThenSameDrainIsAcceptedAfterBinding() {
        assertEquals(
                DrainRequestOwner.Decision.REJECT,
                DrainRequestOwner.decide(
                        null, null, "phone-node", "stop-123",
                        "capture-456", 7L, 900L, 8_000L));
        assertEquals(
                DrainRequestOwner.Decision.ACCEPT_NEW,
                DrainRequestOwner.decide(
                        null, binding(900L), "phone-node", "stop-123",
                        "capture-456", 7L, 900L, 8_000L));
    }

    @Test
    public void sameOwnerRebasesOnlyToCurrentReplacementSession() {
        PhoneStreamBinding oldBinding = binding(900L);
        DrainRequestOwner owner = DrainRequestOwner.create(
                oldBinding, "stop-123", 900L, 8_000L);
        PhoneStreamBinding replacement = binding(901L);
        assertEquals(
                DrainRequestOwner.Decision.ACCEPT_DUPLICATE,
                DrainRequestOwner.decide(
                        owner, replacement, "phone-node", "stop-123",
                        "capture-456", 7L, 901L, 8_000L));
        assertEquals(
                DrainRequestOwner.Decision.REJECT,
                DrainRequestOwner.decide(
                        owner, replacement, "phone-node", "stop-123",
                        "capture-456", 7L, 900L, 8_000L));
        assertTrue(owner.matchesAck(
                replacement, "phone-node", "stop-123",
                "capture-456", 7L, 901L));
    }

    @Test
    public void firstDrainOwnerRejectsDifferentLaterStopUuid() {
        PhoneStreamBinding binding = binding(900L);
        DrainRequestOwner owner = DrainRequestOwner.create(
                binding, "stop-first", 900L, 8_000L);
        assertEquals(
                DrainRequestOwner.Decision.REJECT,
                DrainRequestOwner.decide(
                        owner, binding, "phone-node", "stop-second",
                        "capture-456", 7L, 900L, 8_000L));
    }
}
