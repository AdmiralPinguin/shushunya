package com.shushunya.m.wear.audio;

/** Places GAP on the first frame whose source sequence is actually discontinuous. */
final class FrameContinuityPolicy {
    private FrameContinuityPolicy() {}

    static int flagsFor(
            int baseFlags,
            long previousFlushedUnsignedSequence,
            int currentSequence,
            boolean forceGap) {
        long current = Integer.toUnsignedLong(currentSequence);
        boolean discontinuity;
        if (previousFlushedUnsignedSequence < 0L) {
            discontinuity = current != 0L;
        } else {
            long expected = (previousFlushedUnsignedSequence + 1L)
                    & WearAudioLifecycleProtocol.MAX_UNSIGNED_SEQUENCE;
            discontinuity = current != expected;
        }
        return forceGap || discontinuity
                ? baseFlags | WearAudioProtocol.FLAG_GAP_BEFORE
                : baseFlags;
    }
}
