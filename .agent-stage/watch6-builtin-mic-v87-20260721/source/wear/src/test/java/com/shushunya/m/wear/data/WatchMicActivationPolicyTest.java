package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class WatchMicActivationPolicyTest {
    @Test
    public void inactiveServiceGetsOneGraceRecheck() {
        assertEquals(
                WatchMicActivationPolicy.Action.SCHEDULE_RECHECK,
                WatchMicActivationPolicy.decide(true, false, false));
    }

    @Test
    public void serviceThatWinsRaceCancelsWarning() {
        assertEquals(
                WatchMicActivationPolicy.Action.CANCEL_WARNING,
                WatchMicActivationPolicy.decide(true, true, true));
    }

    @Test
    public void missingServiceAfterGraceIsReported() {
        assertEquals(
                WatchMicActivationPolicy.Action.REPORT_MISSING,
                WatchMicActivationPolicy.decide(true, false, true));
    }

    @Test
    public void stoppedPhoneCancelsPendingWarning() {
        assertEquals(
                WatchMicActivationPolicy.Action.CANCEL_WARNING,
                WatchMicActivationPolicy.decide(false, false, true));
    }

    @Test
    public void asynchronousStoppedSnapshotCannotTearDownUserStartedCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.KEEP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        false, false, false, false, false, false));
    }

    @Test
    public void asynchronousRunningSnapshotKeepsCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.KEEP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        true, false, false, false, false, false));
    }

    @Test
    public void exactToggleOffAcknowledgementStopsCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.STOP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        false, true, true, true, false, false));
    }

    @Test
    public void exactToggleOnAcknowledgementKeepsCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.KEEP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        true, true, true, true, true, false));
    }

    @Test
    public void exactFailedStartStopsCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.STOP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        false, true, true, true, true, true));
    }

    @Test
    public void exactFailedStopKeepsExistingCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.KEEP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        true, true, true, true, false, true));
    }

    @Test
    public void missingDirectionCannotOwnCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.KEEP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        false, true, true, false, false, true));
    }

    @Test
    public void delayedAWhileBIsPendingCannotOwnCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.KEEP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        false, false, true, true, true, true));
    }

    @Test
    public void nonterminalExactObservationCannotOwnCapture() {
        assertEquals(
                WatchMicActivationPolicy.CaptureAction.KEEP_CAPTURE,
                WatchMicActivationPolicy.captureAction(
                        false, true, false, true, true, true));
    }
}
