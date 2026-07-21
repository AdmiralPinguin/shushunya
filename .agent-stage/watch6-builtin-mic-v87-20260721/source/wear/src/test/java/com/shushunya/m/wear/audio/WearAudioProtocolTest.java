package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;

public final class WearAudioProtocolTest {
    @Test
    public void headerAndFrameMatchBigEndianMetadataAndLittleEndianPcm() throws Exception {
        long sessionId = 0x0102030405060708L;
        int sequence = 0x11223344;
        long capturedAt = 0x1020304050607080L;
        byte[] pcm = new byte[WearAudioProtocol.PCM_BYTES_PER_FRAME];
        pcm[0] = 0x34;
        pcm[1] = 0x12;
        pcm[pcm.length - 2] = (byte) 0xcd;
        pcm[pcm.length - 1] = (byte) 0xab;

        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            WearAudioProtocol.writeHeader(output, sessionId);
            WearAudioProtocol.writeFrame(
                    output,
                    sequence,
                    capturedAt,
                    WearAudioProtocol.FLAG_GAP_BEFORE,
                    pcm);
        }

        assertEquals(24 + 16 + WearAudioProtocol.PCM_BYTES_PER_FRAME, bytes.size());
        try (DataInputStream input = new DataInputStream(
                new ByteArrayInputStream(bytes.toByteArray()))) {
            assertEquals(WearAudioProtocol.STREAM_MAGIC, input.readInt());
            assertEquals(WearAudioProtocol.VERSION, input.readInt());
            assertEquals(WearAudioProtocol.SAMPLE_RATE, input.readInt());
            assertEquals(WearAudioProtocol.FRAME_SAMPLES, input.readInt());
            assertEquals(sessionId, input.readLong());
            assertEquals(sequence, input.readInt());
            assertEquals(capturedAt, input.readLong());
            assertEquals(WearAudioProtocol.FRAME_SAMPLES, input.readUnsignedShort());
            assertEquals(WearAudioProtocol.FLAG_GAP_BEFORE, input.readUnsignedShort());
            byte[] actualPcm = new byte[WearAudioProtocol.PCM_BYTES_PER_FRAME];
            input.readFully(actualPcm);
            assertArrayEquals(pcm, actualPcm);
            assertEquals(-1, input.read());
        }
    }

    @Test
    public void refusesAnyNonExactPcmFrame() {
        assertThrows(IllegalArgumentException.class, () ->
                WearAudioProtocol.writeFrame(
                        new DataOutputStream(new ByteArrayOutputStream()),
                        0,
                        1L,
                        0,
                        new byte[WearAudioProtocol.PCM_BYTES_PER_FRAME - 2]));
    }

    @Test
    public void refusesNonPositiveSessionHeader() {
        assertThrows(IllegalArgumentException.class, () ->
                WearAudioProtocol.writeHeader(
                        new DataOutputStream(new ByteArrayOutputStream()), 0L));
        assertThrows(IllegalArgumentException.class, () ->
                WearAudioProtocol.writeHeader(
                        new DataOutputStream(new ByteArrayOutputStream()), -1L));
    }
}
