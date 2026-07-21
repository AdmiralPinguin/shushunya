package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;

public final class WearAudioTerminalAckRegistryTest {
    private PhoneStreamBinding binding;
    private DrainRequestOwner drain;

    @Before
    public void setUp() {
        WearAudioTerminalAckRegistry.clear();
        binding = new PhoneStreamBinding(
                "phone-node", "bind-capture", "capture", 3L, 90L);
        drain = DrainRequestOwner.create(binding, "stop-capture", 90L, 8_000L);
    }

    @After
    public void tearDown() {
        WearAudioTerminalAckRegistry.clear();
    }

    @Test
    public void unsolicitedAckBeforeTerminalIsRejected() {
        assertFalse(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, 73L, "", 1_000L));
    }

    @Test
    public void exactOwnerAndExactSequenceUnlockGracefulTerminal() throws Exception {
        assertTrue(WearAudioTerminalAckRegistry.expect(
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS, 73L));
        assertTrue(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, 73L, "", 1_000L));
        WearAudioTerminalAckRegistry.Ack ack =
                WearAudioTerminalAckRegistry.await(1L);
        assertNotNull(ack);
        assertTrue(ack.confirmsGraceful(73L));
    }

    @Test
    public void mismatchedSequenceAndOwnerDoNotUnblock() throws Exception {
        assertTrue(WearAudioTerminalAckRegistry.expect(
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS, 73L));
        assertFalse(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, 72L, "", 1_000L));
        assertFalse(record("phone-node", "bind-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, 73L, "", 1_000L));
        assertFalse(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, 73L, "archive error", 1_000L));
        assertTrue(WearAudioTerminalAckRegistry.await(1L) == null);
    }

    @Test
    public void exactDuplicateAckIsIdempotentAndConflictIsRejected() throws Exception {
        assertTrue(WearAudioTerminalAckRegistry.expect(
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS, -1L));
        assertTrue(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, -1L, "", 1_000L));
        assertTrue(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, -1L, "", 1_000L));
        assertFalse(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, -1L, "", 1_001L));
        assertTrue(WearAudioTerminalAckRegistry.await(1L).confirmsGraceful(-1L));
    }

    @Test
    public void hardTerminalAcceptsOnlyExactAbortedAck() throws Exception {
        assertTrue(WearAudioTerminalAckRegistry.expect(
                WearAudioLifecycleProtocol.DISPOSITION_HARD_FAILURE, 12L));
        assertFalse(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, 12L, "", 1_000L));
        assertTrue(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_ABORTED, 12L,
                "capture route failed", 1_000L));
        assertNotNull(WearAudioTerminalAckRegistry.await(1L));
    }

    @Test
    public void gracefulTerminalAcceptsExactNegativeReceiptWithoutFalseFinish()
            throws Exception {
        assertTrue(WearAudioTerminalAckRegistry.expect(
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS, 73L));
        // Crash recovery may only prove that the phone archived through 41.
        // This must end retrying as an explicit failure, never as FINISHED.
        assertTrue(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_ABORTED, 41L,
                "phone restarted before durable FINISH", 1_000L));
        WearAudioTerminalAckRegistry.Ack ack =
                WearAudioTerminalAckRegistry.await(1L);
        assertNotNull(ack);
        assertFalse(ack.confirmsGraceful(73L));
    }

    @Test
    public void abortedReceiptWithoutReasonIsRejected() throws Exception {
        assertTrue(WearAudioTerminalAckRegistry.expect(
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS, 73L));
        assertFalse(record("phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_ABORTED, 41L, "", 1_000L));
        assertTrue(WearAudioTerminalAckRegistry.await(1L) == null);
    }

    @Test
    public void replacementBindingOwnsTerminalAndOldSessionAckIsRejected() {
        PhoneStreamBinding replacement = new PhoneStreamBinding(
                "phone-node", "bind-capture", "capture", 3L, 91L);
        assertTrue(WearAudioTerminalAckRegistry.expect(
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS, 99L));
        assertFalse(WearAudioTerminalAckRegistry.record(
                replacement, drain,
                "phone-node", "stop-capture", "capture", 3L, 90L,
                WearAudioLifecycleProtocol.ACK_FINISHED, 99L, "", 1_000L));
        assertTrue(WearAudioTerminalAckRegistry.record(
                replacement, drain,
                "phone-node", "stop-capture", "capture", 3L, 91L,
                WearAudioLifecycleProtocol.ACK_FINISHED, 99L, "", 1_000L));
    }

    private boolean record(
            String node,
            String request,
            String group,
            long generation,
            long session,
            String disposition,
            long sequence,
            String error,
            long ackAtMs) {
        return WearAudioTerminalAckRegistry.record(
                binding, drain, node, request, group, generation, session,
                disposition, sequence, error, ackAtMs);
    }
}
