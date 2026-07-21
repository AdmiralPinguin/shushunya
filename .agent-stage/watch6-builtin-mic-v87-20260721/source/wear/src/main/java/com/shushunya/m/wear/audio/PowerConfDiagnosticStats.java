package com.shushunya.m.wear.audio;

/** Small allocation-free PCM16 meter used by the on-watch raw capture probe. */
final class PowerConfDiagnosticStats {
    private long samples;
    private long nonzeroSamples;
    private long clippedSamples;
    private long sumSquares;
    private int peak;

    void observe(short[] pcm, int offset, int length) {
        if (pcm == null || offset < 0 || length < 0 || offset + length > pcm.length) {
            throw new IllegalArgumentException("invalid PCM slice");
        }
        for (int index = offset; index < offset + length; index++) {
            int sample = pcm[index];
            int magnitude = sample == Short.MIN_VALUE ? 32768 : Math.abs(sample);
            samples++;
            if (sample != 0) nonzeroSamples++;
            if (magnitude >= 32767) clippedSamples++;
            if (magnitude > peak) peak = magnitude;
            sumSquares += (long) sample * sample;
        }
    }

    long samples() {
        return samples;
    }

    long nonzeroSamples() {
        return nonzeroSamples;
    }

    long clippedSamples() {
        return clippedSamples;
    }

    int peak() {
        return peak;
    }

    double rms() {
        return samples == 0L ? 0.0 : Math.sqrt((double) sumSquares / samples);
    }

    boolean isAllZero() {
        return samples > 0L && nonzeroSamples == 0L;
    }
}
