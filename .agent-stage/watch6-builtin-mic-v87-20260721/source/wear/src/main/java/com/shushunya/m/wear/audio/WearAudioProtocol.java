package com.shushunya.m.wear.audio;

import java.io.DataOutputStream;
import java.io.IOException;

/** Binary contract for the continuous Watch6 microphone channel. */
public final class WearAudioProtocol {
    public static final String CHANNEL_PATH = "/shushunya/audio/watch/v1";
    public static final int STREAM_MAGIC = 0x53574831; // "SWH1"
    public static final int VERSION = 1;
    public static final int SAMPLE_RATE = 16_000;
    public static final int FRAME_SAMPLES = 320; // 20 ms
    public static final int PCM_BYTES_PER_FRAME = FRAME_SAMPLES * 2;
    public static final int FLAG_GAP_BEFORE = 1;

    private WearAudioProtocol() {}

    /** DataOutputStream writes the framing fields big-endian by contract. */
    public static void writeHeader(DataOutputStream output, long sessionId) throws IOException {
        if (output == null) throw new IllegalArgumentException("output == null");
        if (sessionId <= 0L) throw new IllegalArgumentException("sessionId <= 0");
        output.writeInt(STREAM_MAGIC);
        output.writeInt(VERSION);
        output.writeInt(SAMPLE_RATE);
        output.writeInt(FRAME_SAMPLES);
        output.writeLong(sessionId);
    }

    /** PCM is already little-endian; only the frame metadata is big-endian. */
    public static void writeFrame(
            DataOutputStream output,
            int sequence,
            long captureElapsedNanos,
            int flags,
            byte[] pcmLittleEndian) throws IOException {
        if (output == null) throw new IllegalArgumentException("output == null");
        if (captureElapsedNanos <= 0L) {
            throw new IllegalArgumentException("captureElapsedNanos <= 0");
        }
        if (flags < 0 || flags > 0xffff) {
            throw new IllegalArgumentException("flags outside unsigned short");
        }
        if (pcmLittleEndian == null || pcmLittleEndian.length != PCM_BYTES_PER_FRAME) {
            throw new IllegalArgumentException("PCM frame must contain exactly "
                    + PCM_BYTES_PER_FRAME + " bytes");
        }
        output.writeInt(sequence);
        output.writeLong(captureElapsedNanos);
        output.writeShort(FRAME_SAMPLES);
        output.writeShort(flags);
        output.write(pcmLittleEndian);
    }
}
