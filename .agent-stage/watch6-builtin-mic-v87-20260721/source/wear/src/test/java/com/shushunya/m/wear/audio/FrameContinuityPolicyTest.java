package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class FrameContinuityPolicyTest {
    @Test
    public void gapLandsOnFirstSurvivingDiscontinuousFrame() {
        assertEquals(
                WearAudioProtocol.FLAG_GAP_BEFORE,
                FrameContinuityPolicy.flagsFor(0, 5L, 7, false));
        assertEquals(
                0,
                FrameContinuityPolicy.flagsFor(0, 7L, 8, false));
    }

    @Test
    public void replacementCanForceGapWithoutRacingProducer() {
        assertEquals(
                WearAudioProtocol.FLAG_GAP_BEFORE,
                FrameContinuityPolicy.flagsFor(0, 5L, 6, true));
    }

    @Test
    public void unsignedSequenceWrapIsContinuous() {
        assertEquals(
                0,
                FrameContinuityPolicy.flagsFor(
                        0, WearAudioLifecycleProtocol.MAX_UNSIGNED_SEQUENCE, 0, false));
    }
}
