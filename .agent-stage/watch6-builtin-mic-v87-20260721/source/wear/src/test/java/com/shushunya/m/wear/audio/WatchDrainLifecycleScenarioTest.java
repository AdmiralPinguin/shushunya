package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import org.junit.After;
import org.junit.Test;

/** Cross-policy regression for replacement while the same STOP is draining. */
public final class WatchDrainLifecycleScenarioTest {
    @After
    public void clearAckMailbox() {
        WearAudioTerminalAckRegistry.clear();
    }

    @Test
    public void oldDrainReplacementBindingSameDrainTerminalAndAckUseNewSession()
            throws Exception {
        PhoneStreamBinding oldBinding = new PhoneStreamBinding(
                "phone-node", "bind-capture", "capture", 7L, 900L);
        assertEquals(
                DrainRequestOwner.Decision.ACCEPT_NEW,
                DrainRequestOwner.decide(
                        null, oldBinding, "phone-node", "stop-capture",
                        "capture", 7L, 900L, 8_000L));
        DrainRequestOwner owner = DrainRequestOwner.create(
                oldBinding, "stop-capture", 900L, 8_000L);

        PhoneStreamBinding replacement = new PhoneStreamBinding(
                "phone-node", "bind-capture", "capture", 7L, 901L);
        assertEquals(
                DrainRequestOwner.Decision.ACCEPT_DUPLICATE,
                DrainRequestOwner.decide(
                        owner, replacement, "phone-node", "stop-capture",
                        "capture", 7L, 901L, 8_000L));

        String terminal = WearAudioLifecycleProtocol.terminalJson(
                owner.requestId,
                replacement.captureGroupId,
                replacement.runGeneration,
                replacement.sessionId,
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS,
                73L,
                0L,
                "");
        assertTrue(terminal.contains("\"sessionId\":901"));
        assertTrue(WearAudioTerminalAckRegistry.expect(
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS, 73L));
        assertTrue(WearAudioTerminalAckRegistry.record(
                replacement,
                owner,
                "phone-node",
                "stop-capture",
                "capture",
                7L,
                901L,
                WearAudioLifecycleProtocol.ACK_FINISHED,
                73L,
                "",
                1_234L));
        WearAudioTerminalAckRegistry.Ack ack =
                WearAudioTerminalAckRegistry.await(1L);
        assertNotNull(ack);
        assertTrue(ack.confirmsGraceful(73L));
    }
}
