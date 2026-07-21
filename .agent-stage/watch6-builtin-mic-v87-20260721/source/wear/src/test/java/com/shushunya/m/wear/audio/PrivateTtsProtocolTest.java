package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

public final class PrivateTtsProtocolTest {
    @Test
    public void phoneWireBytesAndRoundTripAreExact() throws Exception {
        long generation = 0x0102030405060708L;
        long clipId = 0x1112131415161718L;
        byte[] pcm = {0x34, 0x12, (byte) 0xcd, (byte) 0xab};
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            PrivateTtsProtocol.writePhoneHeader(output, generation);
            PrivateTtsProtocol.writeBegin(
                    output, clipId, PrivateTtsProtocol.PURPOSE_READINESS);
            PrivateTtsProtocol.writePcm(output, clipId, 0, pcm);
            PrivateTtsProtocol.writeEnd(output, clipId, 1);
            PrivateTtsProtocol.writeAbort(output, clipId);
            PrivateTtsProtocol.writeStreamEnd(output);
        }

        byte[] wire = bytes.toByteArray();
        assertArrayEquals(new byte[] {
                0x53, 0x50, 0x54, 0x31, 0, 0, 0, 1,
                1, 2, 3, 4, 5, 6, 7, 8,
                1, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 1,
                2, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18,
                0, 0, 0, 0, 0, 0, 0, 4, 0x34, 0x12, (byte) 0xcd, (byte) 0xab,
                3, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18,
                0, 0, 0, 1,
                4, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18,
                5
        }, wire);

        try (DataInputStream input = new DataInputStream(new ByteArrayInputStream(wire))) {
            assertEquals(generation, PrivateTtsProtocol.readPhoneHeader(input).generation);
            PrivateTtsProtocol.Record begin = PrivateTtsProtocol.readRecord(input);
            assertEquals(PrivateTtsProtocol.TYPE_BEGIN, begin.type);
            assertEquals(clipId, begin.clipId);
            assertEquals(PrivateTtsProtocol.PURPOSE_READINESS, begin.purpose);

            PrivateTtsProtocol.Record audio = PrivateTtsProtocol.readRecord(input);
            assertEquals(PrivateTtsProtocol.TYPE_PCM, audio.type);
            assertEquals(0, audio.sequence);
            assertArrayEquals(pcm, audio.pcm);

            PrivateTtsProtocol.Record end = PrivateTtsProtocol.readRecord(input);
            assertEquals(PrivateTtsProtocol.TYPE_END, end.type);
            assertEquals(1, end.sequence);
            assertEquals(PrivateTtsProtocol.TYPE_ABORT,
                    PrivateTtsProtocol.readRecord(input).type);
            assertEquals(PrivateTtsProtocol.TYPE_STREAM_END,
                    PrivateTtsProtocol.readRecord(input).type);
            assertNull(PrivateTtsProtocol.readRecord(input));
        }
    }

    @Test
    public void ackWireBytesUtf8AndRoundTripAreExact() throws Exception {
        long generation = 7L;
        long clipId = 9L;
        long elapsed = 0x0102030405060708L;
        String detail = "SoundForm · готово";
        byte[] detailBytes = detail.getBytes(StandardCharsets.UTF_8);
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            PrivateTtsProtocol.writeAckHeader(output, generation);
            PrivateTtsProtocol.writeAck(
                    output,
                    PrivateTtsProtocol.ACK_FIRST_AUDIO,
                    clipId,
                    elapsed,
                    detail);
        }

        byte[] wire = bytes.toByteArray();
        assertArrayEquals(new byte[] {0x53, 0x50, 0x41, 0x31},
                Arrays.copyOfRange(wire, 0, 4));
        assertEquals(PrivateTtsProtocol.ACK_FIRST_AUDIO, wire[16] & 0xff);
        assertEquals(detailBytes.length,
                ((wire[33] & 0xff) << 8) | (wire[34] & 0xff));
        assertArrayEquals(detailBytes, Arrays.copyOfRange(wire, 35, wire.length));

        try (DataInputStream input = new DataInputStream(new ByteArrayInputStream(wire))) {
            assertEquals(generation, PrivateTtsProtocol.readAckHeader(input).generation);
            PrivateTtsProtocol.Ack ack = PrivateTtsProtocol.readAck(input);
            assertEquals(PrivateTtsProtocol.ACK_FIRST_AUDIO, ack.type);
            assertEquals(clipId, ack.clipId);
            assertEquals(elapsed, ack.watchElapsedNanos);
            assertEquals(detail, ack.detail);
            assertNull(PrivateTtsProtocol.readAck(input));
        }
    }

    @Test
    public void validatorAcceptsOrderedClipsAbortAndTerminalEof() throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            PrivateTtsProtocol.writeBegin(
                    output, 1L, PrivateTtsProtocol.PURPOSE_READINESS);
            PrivateTtsProtocol.writePcm(output, 1L, 0, new byte[] {1, 0});
            PrivateTtsProtocol.writeEnd(output, 1L, 1);
            PrivateTtsProtocol.writeBegin(
                    output, 2L, PrivateTtsProtocol.PURPOSE_RU_TRANSLATION);
            PrivateTtsProtocol.writeAbort(output, 2L);
            PrivateTtsProtocol.writeStreamEnd(output);
        }

        PrivateTtsProtocol.PhoneStreamValidator validator =
                new PrivateTtsProtocol.PhoneStreamValidator();
        try (DataInputStream input = new DataInputStream(
                new ByteArrayInputStream(bytes.toByteArray()))) {
            PrivateTtsProtocol.Record record;
            while ((record = PrivateTtsProtocol.readRecord(input)) != null) {
                validator.accept(record);
            }
            validator.accept(null);
        }
        assertTrue(validator.isStreamEnded());
        assertFalse(validator.hasActiveClip());
    }

    @Test
    public void validatorRejectsOrderingClipAndSequenceViolations() throws Exception {
        PrivateTtsProtocol.PhoneStreamValidator noBegin =
                new PrivateTtsProtocol.PhoneStreamValidator();
        assertThrows(PrivateTtsProtocol.ProtocolException.class,
                () -> noBegin.accept(readOnePhoneRecord(output ->
                        PrivateTtsProtocol.writePcm(output, 1L, 0, new byte[] {0, 0}))));

        PrivateTtsProtocol.PhoneStreamValidator wrongSequence =
                new PrivateTtsProtocol.PhoneStreamValidator();
        wrongSequence.accept(readOnePhoneRecord(output ->
                PrivateTtsProtocol.writeBegin(
                        output, 1L, PrivateTtsProtocol.PURPOSE_RU_TRANSLATION)));
        assertThrows(PrivateTtsProtocol.ProtocolException.class,
                () -> wrongSequence.accept(readOnePhoneRecord(output ->
                        PrivateTtsProtocol.writePcm(output, 1L, 1, new byte[] {0, 0}))));

        PrivateTtsProtocol.PhoneStreamValidator wrongClip =
                new PrivateTtsProtocol.PhoneStreamValidator();
        wrongClip.accept(readOnePhoneRecord(output ->
                PrivateTtsProtocol.writeBegin(
                        output, 1L, PrivateTtsProtocol.PURPOSE_READINESS)));
        assertThrows(PrivateTtsProtocol.ProtocolException.class,
                () -> wrongClip.accept(readOnePhoneRecord(output ->
                        PrivateTtsProtocol.writeAbort(output, 2L))));

        PrivateTtsProtocol.PhoneStreamValidator earlyEof =
                new PrivateTtsProtocol.PhoneStreamValidator();
        assertThrows(PrivateTtsProtocol.ProtocolException.class,
                () -> earlyEof.accept(null));
    }

    @Test
    public void rejectsInvalidHeaders() throws Exception {
        assertPhoneHeaderRejected(0x00000000, 1, 1L);
        assertPhoneHeaderRejected(PrivateTtsProtocol.PHONE_MAGIC, 2, 1L);
        assertPhoneHeaderRejected(PrivateTtsProtocol.PHONE_MAGIC, 1, 0L);
        assertAckHeaderRejected(0x00000000, 1, 1L);
        assertAckHeaderRejected(PrivateTtsProtocol.ACK_MAGIC, 2, 1L);
        assertAckHeaderRejected(PrivateTtsProtocol.ACK_MAGIC, 1, -1L);
    }

    @Test
    public void rejectsInvalidPurposeTypeSequenceAndPcmLength() throws Exception {
        assertRecordRejected(new byte[] {1, 0, 0, 0, 0, 0, 0, 0, 1, 0});
        assertRecordRejected(new byte[] {(byte) 99});

        ByteArrayOutputStream negativeSequence = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(negativeSequence)) {
            output.writeByte(PrivateTtsProtocol.TYPE_PCM);
            output.writeLong(1L);
            output.writeInt(-1);
            output.writeInt(2);
            output.writeShort(0);
        }
        assertRecordRejected(negativeSequence.toByteArray());

        for (int invalidLength : new int[] {0, 1, 3, 32_770}) {
            ByteArrayOutputStream badLength = new ByteArrayOutputStream();
            try (DataOutputStream output = new DataOutputStream(badLength)) {
                output.writeByte(PrivateTtsProtocol.TYPE_PCM);
                output.writeLong(1L);
                output.writeInt(0);
                output.writeInt(invalidLength);
            }
            assertRecordRejected(badLength.toByteArray());
        }
    }

    @Test
    public void rejectsAckTypeClipTimestampDetailAndMalformedUtf8() throws Exception {
        assertThrows(PrivateTtsProtocol.ProtocolException.class, () ->
                PrivateTtsProtocol.writeAck(
                        dataOutput(), 9, 0L, 1L, ""));
        assertThrows(PrivateTtsProtocol.ProtocolException.class, () ->
                PrivateTtsProtocol.writeAck(
                        dataOutput(), PrivateTtsProtocol.ACK_READY, 1L, 1L, ""));
        assertThrows(PrivateTtsProtocol.ProtocolException.class, () ->
                PrivateTtsProtocol.writeAck(
                        dataOutput(), PrivateTtsProtocol.ACK_DRAINED, 0L, 1L, ""));
        assertThrows(PrivateTtsProtocol.ProtocolException.class, () ->
                PrivateTtsProtocol.writeAck(
                        dataOutput(), PrivateTtsProtocol.ACK_ERROR, 0L, 0L, ""));
        assertThrows(PrivateTtsProtocol.ProtocolException.class, () ->
                PrivateTtsProtocol.writeAck(
                        dataOutput(), PrivateTtsProtocol.ACK_ERROR, 0L, 1L,
                        "x".repeat(PrivateTtsProtocol.MAX_ACK_DETAIL_BYTES + 1)));

        ByteArrayOutputStream malformed = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(malformed)) {
            output.writeByte(PrivateTtsProtocol.ACK_ERROR);
            output.writeLong(0L);
            output.writeLong(1L);
            output.writeShort(2);
            output.writeByte(0xc3);
            output.writeByte(0x28);
        }
        try (DataInputStream input = new DataInputStream(
                new ByteArrayInputStream(malformed.toByteArray()))) {
            assertThrows(PrivateTtsProtocol.ProtocolException.class,
                    () -> PrivateTtsProtocol.readAck(input));
        }
    }

    @Test
    public void cleanEofIsNullButEveryTruncatedShapeIsProtocolError() throws Exception {
        assertNull(PrivateTtsProtocol.readRecord(dataInput(new byte[0])));
        assertNull(PrivateTtsProtocol.readAck(dataInput(new byte[0])));

        ByteArrayOutputStream phoneHeader = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(phoneHeader)) {
            PrivateTtsProtocol.writePhoneHeader(output, 1L);
        }
        byte[] fullHeader = phoneHeader.toByteArray();
        for (int length = 0; length < fullHeader.length; length++) {
            byte[] prefix = Arrays.copyOf(fullHeader, length);
            assertThrows(PrivateTtsProtocol.ProtocolException.class,
                    () -> PrivateTtsProtocol.readPhoneHeader(dataInput(prefix)));
        }

        ByteArrayOutputStream pcm = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(pcm)) {
            PrivateTtsProtocol.writePcm(output, 1L, 0, new byte[] {1, 0, 2, 0});
        }
        byte[] fullPcm = pcm.toByteArray();
        for (int length = 1; length < fullPcm.length; length++) {
            byte[] prefix = Arrays.copyOf(fullPcm, length);
            assertThrows(PrivateTtsProtocol.ProtocolException.class,
                    () -> PrivateTtsProtocol.readRecord(dataInput(prefix)));
        }

        ByteArrayOutputStream ack = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(ack)) {
            PrivateTtsProtocol.writeAck(
                    output, PrivateTtsProtocol.ACK_ERROR, 0L, 1L, "failure");
        }
        byte[] fullAck = ack.toByteArray();
        for (int length = 1; length < fullAck.length; length++) {
            byte[] prefix = Arrays.copyOf(fullAck, length);
            assertThrows(PrivateTtsProtocol.ProtocolException.class,
                    () -> PrivateTtsProtocol.readAck(dataInput(prefix)));
        }
    }

    private static void assertPhoneHeaderRejected(int magic, int version, long generation)
            throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            output.writeInt(magic);
            output.writeInt(version);
            output.writeLong(generation);
        }
        assertThrows(PrivateTtsProtocol.ProtocolException.class,
                () -> PrivateTtsProtocol.readPhoneHeader(dataInput(bytes.toByteArray())));
    }

    private static void assertAckHeaderRejected(int magic, int version, long generation)
            throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            output.writeInt(magic);
            output.writeInt(version);
            output.writeLong(generation);
        }
        assertThrows(PrivateTtsProtocol.ProtocolException.class,
                () -> PrivateTtsProtocol.readAckHeader(dataInput(bytes.toByteArray())));
    }

    private static void assertRecordRejected(byte[] bytes) {
        assertThrows(PrivateTtsProtocol.ProtocolException.class,
                () -> PrivateTtsProtocol.readRecord(dataInput(bytes)));
    }

    private static PrivateTtsProtocol.Record readOnePhoneRecord(Writer writer)
            throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            writer.write(output);
        }
        return PrivateTtsProtocol.readRecord(dataInput(bytes.toByteArray()));
    }

    private static DataInputStream dataInput(byte[] bytes) {
        return new DataInputStream(new ByteArrayInputStream(bytes));
    }

    private static DataOutputStream dataOutput() {
        return new DataOutputStream(new ByteArrayOutputStream());
    }

    private interface Writer {
        void write(DataOutputStream output) throws Exception;
    }
}
