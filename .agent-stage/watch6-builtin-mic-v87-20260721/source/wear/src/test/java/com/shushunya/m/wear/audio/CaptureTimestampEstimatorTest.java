package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class CaptureTimestampEstimatorTest {
    private static final long MS = 1_000_000L;

    @Test
    public void projectsExactCaptureEndWhenHardwareAnchorIsBehind() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                10_020 * MS,
                estimator.estimate(1_600L, true, 1_280L, 10_000 * MS, 10_040 * MS));
    }

    @Test
    public void projectsExactCaptureEndWhenHardwareAnchorIsAhead() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                10_000 * MS,
                estimator.estimate(1_280L, true, 1_600L, 10_020 * MS, 10_040 * MS));
    }

    @Test
    public void temporaryHardwareFailureExtrapolatesFromLastCaptureEnd() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                10_020 * MS,
                estimator.estimate(320L, true, 0L, 10_000 * MS, 10_100 * MS));
        assertEquals(
                10_040 * MS,
                estimator.estimate(640L, false, 0L, 0L, 10_500 * MS));
    }

    @Test
    public void rejectsWrongClockDomainAndUsesSafeFallback() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                100_000 * MS,
                estimator.estimate(320L, true, 0L, 1_000 * MS, 100_000 * MS));
        assertEquals(
                100_020 * MS,
                estimator.estimate(640L, true, 320L, 1_020 * MS, 100_020 * MS));
    }

    @Test
    public void outOfRangeFrameAnchorFallsBackWithoutBreakingMonotonicity() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                10_000 * MS,
                estimator.estimate(320L, true, 160_321L, 10_000 * MS, 10_000 * MS));
        assertEquals(
                10_020 * MS,
                estimator.estimate(640L, false, 0L, 0L, 10_010 * MS));
    }

    @Test
    public void startupCanBufferMissingTimestampThenBackProjectFromSecondFrame() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        // Frame 1 has no hardware timestamp and is held by the capture service.
        // Frame 2 supplies one usable anchor for both buffered frame ends.
        assertEquals(
                10_020 * MS,
                estimator.estimate(320L, true, 640L, 10_040 * MS, 10_030 * MS));
        assertEquals(
                10_040 * MS,
                estimator.estimate(640L, true, 640L, 10_040 * MS, 10_050 * MS));
    }

    @Test
    public void startupFallbackLocksOneSampleClockWhenNoAnchorAppears() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        estimator.lockToFallback();
        assertEquals(
                60_000 * MS,
                estimator.estimate(320L, false, 0L, 0L, 60_000 * MS));
        assertEquals(
                60_020 * MS,
                estimator.estimate(640L, true, 640L, 59_800 * MS, 60_010 * MS));
    }

    @Test
    public void gracefulStopFlushesOneThroughFourStartupFramesMonotonically() {
        for (int frameCount = 1; frameCount <= 4; frameCount++) {
            CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
            estimator.lockToFallback();
            long previous = 0L;
            for (int frame = 1; frame <= frameCount; frame++) {
                long timestamp = estimator.estimate(
                        frame * 320L,
                        false,
                        0L,
                        0L,
                        (70_000L + frame * 20L) * MS);
                assertEquals((70_000L + frame * 20L) * MS, timestamp);
                org.junit.Assert.assertTrue(timestamp > previous);
                previous = timestamp;
            }
        }
    }

    @Test
    public void fourSecondFutureOutlierCannotPoisonHardwareClock() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                10_020 * MS,
                estimator.estimate(320L, true, 320L, 10_020 * MS, 10_030 * MS));
        assertEquals(
                10_040 * MS,
                estimator.estimate(640L, true, 640L, 14_040 * MS, 10_050 * MS));
        assertEquals(
                10_060 * MS,
                estimator.estimate(960L, true, 960L, 10_060 * MS, 10_070 * MS));
    }

    @Test
    public void fullLongFramePositionCrossesUnsigned32BoundaryWithoutWrap() {
        long boundary = 1L << 32;
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                20_020 * MS,
                estimator.estimate(
                        boundary + 320L,
                        true,
                        boundary,
                        20_000 * MS,
                        20_030 * MS));
        assertEquals(
                20_040 * MS,
                estimator.estimate(
                        boundary + 640L,
                        true,
                        boundary + 320L,
                        20_020 * MS,
                        20_050 * MS));
    }

    @Test
    public void regressingAnchorIsIgnoredAndNewSessionCanRestartAtZero() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                30_020 * MS,
                estimator.estimate(1_320L, true, 1_000L, 30_000 * MS, 30_030 * MS));
        assertEquals(
                30_040 * MS,
                estimator.estimate(1_640L, true, 900L, 30_020 * MS, 30_050 * MS));

        CaptureTimestampEstimator restarted = new CaptureTimestampEstimator(16_000);
        assertEquals(
                40_020 * MS,
                restarted.estimate(320L, true, 0L, 40_000 * MS, 40_030 * MS));
    }

    @Test
    public void partialReadsUseTotalEndExclusiveSamplePosition() {
        long samplesRead = 0L;
        samplesRead += 100L;
        samplesRead += 220L;
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        assertEquals(
                50_020 * MS,
                estimator.estimate(samplesRead, true, 0L, 50_000 * MS, 50_030 * MS));
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsFramePositionThatDoesNotAdvance() {
        CaptureTimestampEstimator estimator = new CaptureTimestampEstimator(16_000);
        estimator.estimate(320L, false, 0L, 0L, 10_000 * MS);
        estimator.estimate(320L, false, 0L, 0L, 10_020 * MS);
    }
}
