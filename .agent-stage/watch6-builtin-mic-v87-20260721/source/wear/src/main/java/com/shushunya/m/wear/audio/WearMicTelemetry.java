package com.shushunya.m.wear.audio;

/**
 * Thread-safe, secret-free state for the Watch microphone uplink.
 *
 * <p>The service owns Android logging and notification rendering. Keeping the
 * counters here free of Android dependencies makes the capture invariants
 * unit-testable.</p>
 */
final class WearMicTelemetry {
    enum Stage {
        IDLE,
        PERMISSION_GRANTED,
        FOREGROUND_SERVICE,
        NEARBY_NODE,
        CHANNEL_OPEN,
        AUDIO_ROUTE,
        FIRST_NONZERO_FRAME,
        STOPPED,
        ERROR
    }

    private Stage stage = Stage.IDLE;
    private String audioSource = "";
    private long capturedFrames;
    private long sentFrames;
    private long droppedFrames;
    private double lastRms;
    private boolean firstNonzeroFrameSeen;
    private String lastError = "";

    synchronized void reset() {
        stage = Stage.IDLE;
        audioSource = "";
        capturedFrames = 0L;
        sentFrames = 0L;
        droppedFrames = 0L;
        lastRms = 0.0;
        firstNonzeroFrameSeen = false;
        lastError = "";
    }

    synchronized Snapshot advance(Stage next) {
        if (next == null) throw new IllegalArgumentException("next == null");
        if (stage != Stage.ERROR
                && next != Stage.ERROR
                && next != Stage.STOPPED
                && next.ordinal() < stage.ordinal()) {
            return snapshotLocked();
        }
        stage = next;
        return snapshotLocked();
    }

    synchronized Snapshot routeReady(String source) {
        audioSource = source == null ? "" : source.trim();
        firstNonzeroFrameSeen = false;
        stage = Stage.AUDIO_ROUTE;
        return snapshotLocked();
    }

    synchronized FrameObservation captured(short[] samples) {
        if (samples == null || samples.length == 0) {
            throw new IllegalArgumentException("empty PCM frame");
        }
        double sumSquares = 0.0;
        boolean nonzero = false;
        for (short sample : samples) {
            double value = sample;
            sumSquares += value * value;
            nonzero |= sample != 0;
        }
        capturedFrames++;
        lastRms = Math.sqrt(sumSquares / samples.length);
        boolean firstNonzero = nonzero && !firstNonzeroFrameSeen;
        if (firstNonzero) {
            firstNonzeroFrameSeen = true;
            stage = Stage.FIRST_NONZERO_FRAME;
        }
        return new FrameObservation(firstNonzero, snapshotLocked());
    }

    synchronized Snapshot sent() {
        sentFrames++;
        return snapshotLocked();
    }

    synchronized Snapshot dropped() {
        droppedFrames++;
        return snapshotLocked();
    }

    synchronized Snapshot fail(String code, Throwable error) {
        String cleanCode = sanitizeToken(code, "UNKNOWN");
        String errorClass = error == null
                ? "UnknownError"
                : sanitizeToken(error.getClass().getSimpleName(), "UnknownError");
        lastError = cleanCode + ":" + errorClass;
        stage = Stage.ERROR;
        return snapshotLocked();
    }

    synchronized Snapshot snapshot() {
        return snapshotLocked();
    }

    private Snapshot snapshotLocked() {
        return new Snapshot(
                stage,
                audioSource,
                capturedFrames,
                sentFrames,
                droppedFrames,
                lastRms,
                firstNonzeroFrameSeen,
                lastError);
    }

    private static String sanitizeToken(String raw, String fallback) {
        if (raw == null) return fallback;
        String clean = raw.trim().replaceAll("[^A-Za-z0-9_.-]", "_");
        if (clean.isEmpty()) return fallback;
        return clean.length() <= 48 ? clean : clean.substring(0, 48);
    }

    static final class FrameObservation {
        final boolean firstNonzero;
        final Snapshot snapshot;

        FrameObservation(boolean firstNonzero, Snapshot snapshot) {
            this.firstNonzero = firstNonzero;
            this.snapshot = snapshot;
        }
    }

    static final class Snapshot {
        final Stage stage;
        final String audioSource;
        final long capturedFrames;
        final long sentFrames;
        final long droppedFrames;
        final double lastRms;
        final boolean firstNonzeroFrameSeen;
        final String lastError;

        Snapshot(
                Stage stage,
                String audioSource,
                long capturedFrames,
                long sentFrames,
                long droppedFrames,
                double lastRms,
                boolean firstNonzeroFrameSeen,
                String lastError) {
            this.stage = stage;
            this.audioSource = audioSource;
            this.capturedFrames = capturedFrames;
            this.sentFrames = sentFrames;
            this.droppedFrames = droppedFrames;
            this.lastRms = lastRms;
            this.firstNonzeroFrameSeen = firstNonzeroFrameSeen;
            this.lastError = lastError;
        }
    }
}
