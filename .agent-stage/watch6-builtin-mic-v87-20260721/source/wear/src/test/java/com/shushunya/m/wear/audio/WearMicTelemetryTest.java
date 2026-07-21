package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class WearMicTelemetryTest {
    @Test
    public void recordsFirstNonzeroOnlyOnceAndNeverSubstitutesFrames() {
        WearMicTelemetry telemetry = new WearMicTelemetry();
        telemetry.advance(WearMicTelemetry.Stage.PERMISSION_GRANTED);
        telemetry.advance(WearMicTelemetry.Stage.FOREGROUND_SERVICE);
        telemetry.routeReady("UNPROCESSED");

        WearMicTelemetry.FrameObservation silence = telemetry.captured(new short[320]);
        assertFalse(silence.firstNonzero);
        assertFalse(silence.snapshot.firstNonzeroFrameSeen);
        assertEquals(1L, silence.snapshot.capturedFrames);
        assertEquals(0.0, silence.snapshot.lastRms, 0.0);

        short[] signal = new short[320];
        signal[7] = 1_600;
        WearMicTelemetry.FrameObservation first = telemetry.captured(signal);
        assertTrue(first.firstNonzero);
        assertTrue(first.snapshot.firstNonzeroFrameSeen);
        assertEquals(WearMicTelemetry.Stage.FIRST_NONZERO_FRAME, first.snapshot.stage);
        assertTrue(first.snapshot.lastRms > 0.0);

        WearMicTelemetry.FrameObservation second = telemetry.captured(signal);
        assertFalse(second.firstNonzero);
        assertEquals(3L, second.snapshot.capturedFrames);
    }

    @Test
    public void countsCaptureSendAndDropIndependently() {
        WearMicTelemetry telemetry = new WearMicTelemetry();
        telemetry.captured(new short[] { 12, -12 });
        telemetry.captured(new short[] { 20, -20 });
        telemetry.sent();
        telemetry.dropped();

        WearMicTelemetry.Snapshot snapshot = telemetry.snapshot();
        assertEquals(2L, snapshot.capturedFrames);
        assertEquals(1L, snapshot.sentFrames);
        assertEquals(1L, snapshot.droppedFrames);
    }

    @Test
    public void errorTelemetryStoresOnlyBoundedCodeAndClass() {
        WearMicTelemetry telemetry = new WearMicTelemetry();
        WearMicTelemetry.Snapshot snapshot = telemetry.fail(
                "channel open / private-node-id-must-not-follow",
                new IllegalStateException("secret endpoint and node id"));

        assertEquals(WearMicTelemetry.Stage.ERROR, snapshot.stage);
        assertTrue(snapshot.lastError.startsWith("channel_open___private-node-id-must-not-follow"));
        assertTrue(snapshot.lastError.endsWith(":IllegalStateException"));
        assertFalse(snapshot.lastError.contains("secret endpoint"));
    }

    @Test
    public void resetStartsFreshSessionCounters() {
        WearMicTelemetry telemetry = new WearMicTelemetry();
        telemetry.captured(new short[] { 1 });
        telemetry.sent();
        telemetry.dropped();
        telemetry.fail("IO", new RuntimeException("ignored"));
        telemetry.reset();

        WearMicTelemetry.Snapshot snapshot = telemetry.snapshot();
        assertEquals(WearMicTelemetry.Stage.IDLE, snapshot.stage);
        assertEquals(0L, snapshot.capturedFrames);
        assertEquals(0L, snapshot.sentFrames);
        assertEquals(0L, snapshot.droppedFrames);
        assertEquals("", snapshot.lastError);
    }

    @Test
    public void reconnectRequiresANewNonzeroMilestone() {
        WearMicTelemetry telemetry = new WearMicTelemetry();
        telemetry.routeReady("UNPROCESSED");
        assertTrue(telemetry.captured(new short[] { 42 }).firstNonzero);

        WearMicTelemetry.Snapshot reconnected = telemetry.routeReady("UNPROCESSED");
        assertFalse(reconnected.firstNonzeroFrameSeen);
        assertEquals(WearMicTelemetry.Stage.AUDIO_ROUTE, reconnected.stage);
        assertTrue(telemetry.captured(new short[] { 42 }).firstNonzero);
    }
}
