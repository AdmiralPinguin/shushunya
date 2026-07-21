package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class WearAudioLifecycleProtocolTest {
    @Test
    public void goldenBindingMatchesPhoneByteForByte() {
        assertEquals(
                "{\"version\":1,\"requestId\":\"bind-capture-456\","
                        + "\"captureGroupId\":\"capture-456\",\"runGeneration\":7,"
                        + "\"sessionId\":900}",
                WearAudioLifecycleProtocol.bindingJson(
                        "bind-capture-456", "capture-456", 7L, 900L));
    }

    @Test
    public void goldenDrainAndTerminalMatchPhoneByteForByte() {
        assertEquals(
                "{\"version\":1,\"requestId\":\"stop-123\","
                        + "\"captureGroupId\":\"capture-456\",\"runGeneration\":7,"
                        + "\"sessionId\":900,\"timeoutMs\":8000}",
                WearAudioLifecycleProtocol.drainJson(
                        "stop-123", "capture-456", 7L, 900L, 8_000L));
        assertEquals(
                "{\"version\":1,\"requestId\":\"stop-123\","
                        + "\"captureGroupId\":\"capture-456\",\"runGeneration\":7,"
                        + "\"sessionId\":900,\"disposition\":\"graceful_eos\","
                        + "\"lastSequence\":73,\"droppedFrames\":0,\"detail\":\"\"}",
                WearAudioLifecycleProtocol.terminalJson(
                        "stop-123", "capture-456", 7L, 900L,
                        WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS,
                        73L, 0L, ""));
    }

    @Test
    public void goldenAckMatchesPhoneByteForByte() {
        assertEquals(
                "{\"version\":1,\"requestId\":\"stop-123\","
                        + "\"captureGroupId\":\"capture-456\",\"runGeneration\":7,"
                        + "\"sessionId\":900,\"disposition\":\"finished\","
                        + "\"acceptedLastSequence\":73,\"error\":\"\",\"ackAtMs\":1234}",
                WearAudioLifecycleProtocol.terminalAckJson(
                        "stop-123", "capture-456", 7L, 900L,
                        WearAudioLifecycleProtocol.ACK_FINISHED, 73L, "", 1_234L));
    }

    @Test
    public void startupFailureCarriesExactCommandBeforeChannelExists() {
        assertEquals(
                "{\"version\":1,\"requestId\":\"start-123\","
                        + "\"code\":\"POWERCONF_ZERO_PCM\","
                        + "\"detail\":\"zero \\\"PCM\\\"\",\"failedAtMs\":1234}",
                WearAudioLifecycleProtocol.startupFailureJson(
                        "start-123",
                        "POWERCONF_ZERO_PCM",
                        "zero \"PCM\"",
                        1_234L));
        assertEquals("", WearAudioLifecycleProtocol.startupFailureJson(
                "start-123", "bad-code", "", 1_234L));
    }

    @Test
    public void startupFailureAckMatchesPhoneByteForByte() {
        assertEquals(
                "{\"version\":1,\"requestId\":\"start-123\","
                        + "\"code\":\"POWERCONF_ZERO_PCM\","
                        + "\"failedAtMs\":1234,\"ackAtMs\":2345}",
                WearAudioLifecycleProtocol.startupFailureAckJson(
                        "start-123", "POWERCONF_ZERO_PCM", 1_234L, 2_345L));
    }

    @Test
    public void zeroFrameGracefulTerminalUsesNoSequenceSentinel() {
        String payload = WearAudioLifecycleProtocol.terminalJson(
                "stop-zero", "capture-zero", 1L, 2L,
                WearAudioLifecycleProtocol.DISPOSITION_GRACEFUL_EOS,
                -1L, 0L, "");
        assertTrue(payload.contains("\"lastSequence\":-1"));
    }

    @Test
    public void drainAllowsOnlyWildcardOrPositiveSessionAndEightSecondCeiling() {
        assertTrue(WearAudioLifecycleProtocol.drainJson(
                "stop-a", "capture-a", 1L, 0L, 8_000L).length() > 0);
        assertEquals("", WearAudioLifecycleProtocol.drainJson(
                "stop-a", "capture-a", 1L, -1L, 8_000L));
        assertEquals("", WearAudioLifecycleProtocol.drainJson(
                "stop-a", "capture-a", 1L, 1L, 8_001L));
    }
}
