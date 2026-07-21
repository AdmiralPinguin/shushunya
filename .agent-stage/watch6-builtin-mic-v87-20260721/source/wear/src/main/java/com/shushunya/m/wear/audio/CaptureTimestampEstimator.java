package com.shushunya.m.wear.audio;

/**
 * Projects an AudioRecord hardware timestamp onto the end position of each
 * complete PCM frame. All timestamps are expected to use CLOCK_BOOTTIME.
 */
final class CaptureTimestampEstimator {
    private static final long NANOS_PER_SECOND = 1_000_000_000L;
    private static final long MAX_READ_COMPLETION_DISTANCE_NANOS = 750_000_000L;
    private static final long MAX_EXPECTED_CLOCK_CORRECTION_NANOS = 100_000_000L;
    private static final long MAX_CAPTURE_FUTURE_NANOS = 20_000_000L;
    private static final long MAX_ANCHOR_DISTANCE_SECONDS = 5L;

    private final int sampleRate;
    private final long maximumAnchorDistanceFrames;
    private long previousFrameEndPosition = -1L;
    private long previousCaptureEndNanos = -1L;
    private long lastAnchorFramePosition = -1L;
    private long lastAnchorNanos = -1L;
    private boolean hardwareAnchored;
    private boolean fallbackLocked;

    CaptureTimestampEstimator(int sampleRate) {
        if (sampleRate <= 0) throw new IllegalArgumentException("sampleRate <= 0");
        this.sampleRate = sampleRate;
        this.maximumAnchorDistanceFrames = sampleRate * MAX_ANCHOR_DISTANCE_SECONDS;
    }

    /**
     * @param frameEndPosition total samples returned by this AudioRecord through
     *                         the end of the frame being timestamped
     * @param hardwareTimestampValid true only after a successful BOOTTIME query
     * @param anchorFramePosition frame position associated with anchorNanos
     * @param anchorNanos AudioTimestamp.nanoTime in CLOCK_BOOTTIME
     * @param fallbackNanos current elapsedRealtimeNanos value
     */
    long estimate(
            long frameEndPosition,
            boolean hardwareTimestampValid,
            long anchorFramePosition,
            long anchorNanos,
            long fallbackNanos) {
        if (frameEndPosition < 0L) {
            throw new IllegalArgumentException("frameEndPosition < 0");
        }
        if (fallbackNanos <= 0L) throw new IllegalArgumentException("fallbackNanos <= 0");
        if (previousFrameEndPosition >= 0L && frameEndPosition <= previousFrameEndPosition) {
            throw new IllegalArgumentException("frameEndPosition did not advance");
        }

        Long projected = hardwareTimestampValid && !fallbackLocked
                ? projectHardwareTimestamp(
                        frameEndPosition,
                        anchorFramePosition,
                        anchorNanos,
                        fallbackNanos)
                : null;

        long result;
        if (previousFrameEndPosition < 0L) {
            if (projected != null) {
                hardwareAnchored = true;
                result = projected;
                acceptAnchor(anchorFramePosition, anchorNanos);
            } else {
                result = fallbackNanos;
            }
        } else {
            long expectedAdvance = framesToNanos(frameEndPosition - previousFrameEndPosition);
            long extrapolated = safeAdd(previousCaptureEndNanos, expectedAdvance);
            if (hardwareAnchored) {
                // A transient timestamp failure or a regressing vendor anchor must
                // not kick the wire clock back to read-return time.
                boolean anchorMonotonic = isAnchorMonotonic(
                        anchorFramePosition, anchorNanos);
                boolean followsSampleClock = projected != null
                        && projected > previousCaptureEndNanos
                        && absoluteDistance(projected, extrapolated)
                                <= MAX_EXPECTED_CLOCK_CORRECTION_NANOS;
                if (anchorMonotonic && followsSampleClock) {
                    result = projected;
                    acceptAnchor(anchorFramePosition, anchorNanos);
                } else {
                    result = extrapolated;
                }
            } else {
                result = Math.max(fallbackNanos, extrapolated);
            }
        }

        if (result <= 0L) result = fallbackNanos;
        previousFrameEndPosition = frameEndPosition;
        previousCaptureEndNanos = result;
        return result;
    }

    /** Selects one stable software epoch if startup produced no hardware anchor. */
    void lockToFallback() {
        if (previousFrameEndPosition >= 0L || hardwareAnchored) {
            throw new IllegalStateException("capture clock already started");
        }
        fallbackLocked = true;
    }

    boolean isInitialHardwareAnchorUsable(
            long frameEndPosition,
            long anchorFramePosition,
            long anchorNanos,
            long readDoneNanos) {
        if (previousFrameEndPosition >= 0L || fallbackLocked) return false;
        return projectHardwareTimestamp(
                frameEndPosition, anchorFramePosition, anchorNanos, readDoneNanos) != null;
    }

    private Long projectHardwareTimestamp(
            long frameEndPosition,
            long anchorFramePosition,
            long anchorNanos,
            long fallbackNanos) {
        if (anchorFramePosition < 0L || anchorNanos <= 0L) return null;
        final long frameDelta;
        try {
            frameDelta = Math.subtractExact(frameEndPosition, anchorFramePosition);
        } catch (ArithmeticException ignored) {
            return null;
        }
        if (frameDelta < -maximumAnchorDistanceFrames
                || frameDelta > maximumAnchorDistanceFrames) {
            return null;
        }
        long projected = safeAdd(anchorNanos, framesToNanos(frameDelta));
        // This also rejects an accidentally supplied MONOTONIC anchor in a
        // BOOTTIME stream after the device has spent time suspended.
        if (projected <= 0L
                || projected > safeAdd(fallbackNanos, MAX_CAPTURE_FUTURE_NANOS)
                || absoluteDistance(projected, fallbackNanos)
                        > MAX_READ_COMPLETION_DISTANCE_NANOS) {
            return null;
        }
        return projected;
    }

    private boolean isAnchorMonotonic(long anchorFramePosition, long anchorNanos) {
        return anchorFramePosition >= 0L
                && anchorNanos > 0L
                && (lastAnchorFramePosition < 0L
                        || (anchorFramePosition >= lastAnchorFramePosition
                        && anchorNanos >= lastAnchorNanos));
    }

    private void acceptAnchor(long anchorFramePosition, long anchorNanos) {
        lastAnchorFramePosition = anchorFramePosition;
        lastAnchorNanos = anchorNanos;
    }

    private long framesToNanos(long frames) {
        long wholeSeconds = frames / sampleRate;
        long remainderFrames = frames % sampleRate;
        final long wholeNanos;
        final long remainderNanos;
        try {
            wholeNanos = Math.multiplyExact(wholeSeconds, NANOS_PER_SECOND);
            remainderNanos = Math.multiplyExact(remainderFrames, NANOS_PER_SECOND) / sampleRate;
            return Math.addExact(wholeNanos, remainderNanos);
        } catch (ArithmeticException ignored) {
            return frames < 0L ? Long.MIN_VALUE : Long.MAX_VALUE;
        }
    }

    private static long safeAdd(long first, long second) {
        if (second > 0L && first > Long.MAX_VALUE - second) return Long.MAX_VALUE;
        if (second < 0L && first < Long.MIN_VALUE - second) return Long.MIN_VALUE;
        return first + second;
    }

    private static long absoluteDistance(long first, long second) {
        long delta;
        try {
            delta = Math.subtractExact(first, second);
        } catch (ArithmeticException ignored) {
            return Long.MAX_VALUE;
        }
        return delta == Long.MIN_VALUE ? Long.MAX_VALUE : Math.abs(delta);
    }
}
