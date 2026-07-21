package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class ZeroPcmStartupGuardTest {
    @Test
    public void requiresANonzeroSampleBeforePublishingCapture() {
        ZeroPcmStartupGuard guard = new ZeroPcmStartupGuard(3, 1);

        assertEquals(ZeroPcmStartupGuard.Decision.WAIT, guard.observe(new short[320]));
        assertEquals(ZeroPcmStartupGuard.Decision.WAIT, guard.observe(new short[320]));
        short[] signal = new short[320];
        signal[319] = 1;
        assertEquals(ZeroPcmStartupGuard.Decision.PROVEN, guard.observe(signal));
        assertEquals(ZeroPcmStartupGuard.Decision.PROVEN, guard.observe(new short[320]));
    }

    @Test
    public void permitsExactlyOneFullReacquisitionThenFailsClosed() {
        ZeroPcmStartupGuard guard = new ZeroPcmStartupGuard(2, 1);

        assertEquals(ZeroPcmStartupGuard.Decision.WAIT, guard.observe(new short[320]));
        assertEquals(ZeroPcmStartupGuard.Decision.REACQUIRE, guard.observe(new short[320]));
        assertEquals(1, guard.reacquisitions());
        assertEquals(ZeroPcmStartupGuard.Decision.WAIT, guard.observe(new short[320]));
        assertEquals(ZeroPcmStartupGuard.Decision.FAIL, guard.observe(new short[320]));
        assertEquals(1, guard.reacquisitions());
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsEmptyFrames() {
        new ZeroPcmStartupGuard().observe(new short[0]);
    }
}
