package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class PowerConfDiagnosticStatsTest {
    @Test
    public void identifiesDigitalSilenceExactly() {
        PowerConfDiagnosticStats stats = new PowerConfDiagnosticStats();
        stats.observe(new short[320], 0, 320);

        assertEquals(320L, stats.samples());
        assertEquals(0L, stats.nonzeroSamples());
        assertEquals(0, stats.peak());
        assertEquals(0.0, stats.rms(), 0.0);
        assertTrue(stats.isAllZero());
    }

    @Test
    public void reportsSignalPeakRmsAndClipping() {
        PowerConfDiagnosticStats stats = new PowerConfDiagnosticStats();
        short[] pcm = {0, 3, -4, Short.MAX_VALUE, Short.MIN_VALUE};
        stats.observe(pcm, 0, pcm.length);

        assertEquals(5L, stats.samples());
        assertEquals(4L, stats.nonzeroSamples());
        assertEquals(2L, stats.clippedSamples());
        assertEquals(32768, stats.peak());
        assertFalse(stats.isAllZero());
        assertTrue(stats.rms() > 20_000.0);
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsInvalidSlices() {
        new PowerConfDiagnosticStats().observe(new short[2], 1, 2);
    }
}
