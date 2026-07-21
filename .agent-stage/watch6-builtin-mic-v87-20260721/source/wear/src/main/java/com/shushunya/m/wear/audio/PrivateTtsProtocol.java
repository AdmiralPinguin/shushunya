package com.shushunya.m.wear.audio;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;

/**
 * Strict, Android-free codec for private phone-to-Watch TTS playback.
 *
 * <p>All framing fields use the big-endian {@link DataInputStream}/{@link
 * DataOutputStream} representation. Only PCM payload bytes are little-endian
 * signed 16-bit samples at {@value #SAMPLE_RATE} Hz, mono.</p>
 */
public final class PrivateTtsProtocol {
    public static final String CHANNEL_PATH = "/shushunya/audio/private-tts/v1";

    public static final int PHONE_MAGIC = 0x53505431; // "SPT1"
    public static final int ACK_MAGIC = 0x53504131;   // "SPA1"
    public static final int VERSION = 1;

    public static final int SAMPLE_RATE = 24_000;
    public static final int CHANNELS = 1;
    public static final int PCM_BYTES_PER_SAMPLE = 2;
    public static final int ROUTE_PROBE_PCM_BYTES = (SAMPLE_RATE / 50) * PCM_BYTES_PER_SAMPLE;

    public static final int TYPE_BEGIN = 1;
    public static final int TYPE_PCM = 2;
    public static final int TYPE_END = 3;
    public static final int TYPE_ABORT = 4;
    public static final int TYPE_STREAM_END = 5;

    public static final int PURPOSE_READINESS = 1;
    public static final int PURPOSE_RU_TRANSLATION = 2;
    public static final int PURPOSE_PUBLIC_KO_RESERVED = 3;

    public static final int ACK_READY = 1;
    public static final int ACK_FIRST_AUDIO = 2;
    public static final int ACK_DRAINED = 3;
    public static final int ACK_ERROR = 4;

    public static final int MAX_PCM_CHUNK_BYTES = 32_768;
    public static final long MAX_CLIP_PCM_BYTES = 16L * 1024L * 1024L;
    public static final int MAX_ACK_DETAIL_BYTES = 512;

    private PrivateTtsProtocol() {}

    public static Header readPhoneHeader(DataInputStream input) throws IOException {
        requireInput(input);
        try {
            int magic = input.readInt();
            int version = input.readInt();
            long generation = input.readLong();
            if (magic != PHONE_MAGIC) {
                throw new ProtocolException(String.format(
                        "phone header magic mismatch: 0x%08x", magic));
            }
            if (version != VERSION) {
                throw new ProtocolException("unsupported phone protocol version " + version);
            }
            requireGeneration(generation, "phone header");
            return new Header(generation);
        } catch (EOFException error) {
            throw truncated("phone header", error);
        }
    }

    /**
     * Reads one phone record. Returns {@code null} only for a clean EOF exactly
     * between records; an EOF inside any record is a {@link ProtocolException}.
     */
    public static Record readRecord(DataInputStream input) throws IOException {
        requireInput(input);
        int type = input.read();
        if (type < 0) return null;
        try {
            switch (type) {
                case TYPE_BEGIN: {
                    long clipId = input.readLong();
                    int purpose = input.readUnsignedByte();
                    requireClipId(clipId, "BEGIN");
                    requirePurpose(purpose);
                    return Record.begin(clipId, purpose);
                }
                case TYPE_PCM: {
                    long clipId = input.readLong();
                    int sequence = input.readInt();
                    int byteLength = input.readInt();
                    requireClipId(clipId, "PCM");
                    requireSequence(sequence, "PCM");
                    requirePcmLength(byteLength);
                    byte[] pcm = new byte[byteLength];
                    input.readFully(pcm);
                    return Record.pcm(clipId, sequence, pcm);
                }
                case TYPE_END: {
                    long clipId = input.readLong();
                    int nextSequence = input.readInt();
                    requireClipId(clipId, "END");
                    requireSequence(nextSequence, "END");
                    return Record.end(clipId, nextSequence);
                }
                case TYPE_ABORT: {
                    long clipId = input.readLong();
                    requireClipId(clipId, "ABORT");
                    return Record.abort(clipId);
                }
                case TYPE_STREAM_END:
                    return Record.streamEnd();
                default:
                    throw new ProtocolException("unknown phone record type " + type);
            }
        } catch (EOFException error) {
            throw truncated("phone record type " + type, error);
        }
    }

    public static void writePhoneHeader(DataOutputStream output, long generation)
            throws IOException {
        requireOutput(output);
        requireGeneration(generation, "phone header");
        output.writeInt(PHONE_MAGIC);
        output.writeInt(VERSION);
        output.writeLong(generation);
    }

    public static void writeBegin(
            DataOutputStream output, long clipId, int purpose) throws IOException {
        requireOutput(output);
        requireClipId(clipId, "BEGIN");
        requirePurpose(purpose);
        output.writeByte(TYPE_BEGIN);
        output.writeLong(clipId);
        output.writeByte(purpose);
    }

    public static void writePcm(
            DataOutputStream output, long clipId, int sequence, byte[] pcmLittleEndian)
            throws IOException {
        requireOutput(output);
        requireClipId(clipId, "PCM");
        requireSequence(sequence, "PCM");
        if (pcmLittleEndian == null) {
            throw new IllegalArgumentException("PCM payload == null");
        }
        requirePcmLength(pcmLittleEndian.length);
        output.writeByte(TYPE_PCM);
        output.writeLong(clipId);
        output.writeInt(sequence);
        output.writeInt(pcmLittleEndian.length);
        output.write(pcmLittleEndian);
    }

    public static void writeEnd(
            DataOutputStream output, long clipId, int nextSequence) throws IOException {
        requireOutput(output);
        requireClipId(clipId, "END");
        requireSequence(nextSequence, "END");
        output.writeByte(TYPE_END);
        output.writeLong(clipId);
        output.writeInt(nextSequence);
    }

    public static void writeAbort(DataOutputStream output, long clipId) throws IOException {
        requireOutput(output);
        requireClipId(clipId, "ABORT");
        output.writeByte(TYPE_ABORT);
        output.writeLong(clipId);
    }

    public static void writeStreamEnd(DataOutputStream output) throws IOException {
        requireOutput(output);
        output.writeByte(TYPE_STREAM_END);
    }

    public static Header readAckHeader(DataInputStream input) throws IOException {
        requireInput(input);
        try {
            int magic = input.readInt();
            int version = input.readInt();
            long generation = input.readLong();
            if (magic != ACK_MAGIC) {
                throw new ProtocolException(String.format(
                        "ack header magic mismatch: 0x%08x", magic));
            }
            if (version != VERSION) {
                throw new ProtocolException("unsupported ack protocol version " + version);
            }
            requireGeneration(generation, "ack header");
            return new Header(generation);
        } catch (EOFException error) {
            throw truncated("ack header", error);
        }
    }

    public static void writeAckHeader(DataOutputStream output, long generation)
            throws IOException {
        requireOutput(output);
        requireGeneration(generation, "ack header");
        output.writeInt(ACK_MAGIC);
        output.writeInt(VERSION);
        output.writeLong(generation);
    }

    /** Returns {@code null} only for a clean EOF exactly between ACK records. */
    public static Ack readAck(DataInputStream input) throws IOException {
        requireInput(input);
        int type = input.read();
        if (type < 0) return null;
        try {
            long clipId = input.readLong();
            long watchElapsedNanos = input.readLong();
            int detailLength = input.readUnsignedShort();
            requireAckEnvelope(type, clipId, watchElapsedNanos, detailLength);
            byte[] detailBytes = new byte[detailLength];
            input.readFully(detailBytes);
            return new Ack(
                    type,
                    clipId,
                    watchElapsedNanos,
                    decodeUtf8Strict(detailBytes, "ACK detail"));
        } catch (EOFException error) {
            throw truncated("ack record type " + type, error);
        }
    }

    public static void writeAck(
            DataOutputStream output,
            int type,
            long clipId,
            long watchElapsedNanos,
            String detail) throws IOException {
        requireOutput(output);
        byte[] detailBytes = (detail == null ? "" : detail)
                .getBytes(StandardCharsets.UTF_8);
        requireAckEnvelope(type, clipId, watchElapsedNanos, detailBytes.length);
        output.writeByte(type);
        output.writeLong(clipId);
        output.writeLong(watchElapsedNanos);
        output.writeShort(detailBytes.length);
        output.write(detailBytes);
    }

    private static void requireInput(DataInputStream input) {
        if (input == null) throw new IllegalArgumentException("input == null");
    }

    private static void requireOutput(DataOutputStream output) {
        if (output == null) throw new IllegalArgumentException("output == null");
    }

    private static void requireGeneration(long generation, String context)
            throws ProtocolException {
        if (generation <= 0L) {
            throw new ProtocolException(context + " generation must be positive");
        }
    }

    private static void requireClipId(long clipId, String context) throws ProtocolException {
        if (clipId <= 0L) throw new ProtocolException(context + " clipId must be positive");
    }

    private static void requirePurpose(int purpose) throws ProtocolException {
        if (purpose < PURPOSE_READINESS || purpose > PURPOSE_PUBLIC_KO_RESERVED) {
            throw new ProtocolException("unsupported BEGIN purpose " + purpose);
        }
    }

    private static void requireSequence(int sequence, String context)
            throws ProtocolException {
        if (sequence < 0) throw new ProtocolException(context + " sequence must be non-negative");
    }

    private static void requirePcmLength(int byteLength) throws ProtocolException {
        if (byteLength < 2 || byteLength > MAX_PCM_CHUNK_BYTES || (byteLength & 1) != 0) {
            throw new ProtocolException(
                    "PCM byte length must be even and within 2.."
                            + MAX_PCM_CHUNK_BYTES + ", got " + byteLength);
        }
    }

    private static void requireAckEnvelope(
            int type, long clipId, long watchElapsedNanos, int detailLength)
            throws ProtocolException {
        if (type < ACK_READY || type > ACK_ERROR) {
            throw new ProtocolException("unknown ACK type " + type);
        }
        if (type == ACK_READY) {
            if (clipId != 0L) throw new ProtocolException("READY clipId must be zero");
        } else if (type == ACK_ERROR) {
            if (clipId < 0L) throw new ProtocolException("ERROR clipId must not be negative");
        } else if (clipId <= 0L) {
            throw new ProtocolException("clip ACK requires a positive clipId");
        }
        if (watchElapsedNanos <= 0L) {
            throw new ProtocolException("watchElapsedNanos must be positive");
        }
        if (detailLength < 0 || detailLength > MAX_ACK_DETAIL_BYTES) {
            throw new ProtocolException(
                    "ACK detail length exceeds " + MAX_ACK_DETAIL_BYTES + ": " + detailLength);
        }
    }

    private static String decodeUtf8Strict(byte[] bytes, String context)
            throws ProtocolException {
        try {
            CharBuffer decoded = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes));
            return decoded.toString();
        } catch (CharacterCodingException error) {
            throw new ProtocolException(context + " is not valid UTF-8", error);
        }
    }

    private static ProtocolException truncated(String context, EOFException error) {
        return new ProtocolException("truncated " + context, error);
    }

    public static final class Header {
        public final long generation;

        private Header(long generation) {
            this.generation = generation;
        }
    }

    public static final class Record {
        public final int type;
        public final long clipId;
        public final int purpose;
        /** PCM sequence, or END's declared next sequence; otherwise -1. */
        public final int sequence;
        public final byte[] pcm;

        private Record(
                int type, long clipId, int purpose, int sequence, byte[] pcm) {
            this.type = type;
            this.clipId = clipId;
            this.purpose = purpose;
            this.sequence = sequence;
            this.pcm = pcm;
        }

        private static Record begin(long clipId, int purpose) {
            return new Record(TYPE_BEGIN, clipId, purpose, -1, null);
        }

        private static Record pcm(long clipId, int sequence, byte[] pcm) {
            return new Record(TYPE_PCM, clipId, 0, sequence, pcm);
        }

        private static Record end(long clipId, int nextSequence) {
            return new Record(TYPE_END, clipId, 0, nextSequence, null);
        }

        private static Record abort(long clipId) {
            return new Record(TYPE_ABORT, clipId, 0, -1, null);
        }

        private static Record streamEnd() {
            return new Record(TYPE_STREAM_END, 0L, 0, -1, null);
        }
    }

    public static final class Ack {
        public final int type;
        public final long clipId;
        public final long watchElapsedNanos;
        public final String detail;

        private Ack(
                int type,
                long clipId,
                long watchElapsedNanos,
                String detail) {
            this.type = type;
            this.clipId = clipId;
            this.watchElapsedNanos = watchElapsedNanos;
            this.detail = detail;
        }
    }

    /** Stateful ordering/sequence validator for records returned by {@link #readRecord}. */
    public static final class PhoneStreamValidator {
        private long activeClipId;
        private int expectedSequence;
        private long activeClipBytes;
        private boolean streamEnded;

        public void accept(Record record) throws ProtocolException {
            if (record == null) {
                if (!streamEnded) {
                    throw new ProtocolException("phone stream ended before STREAM_END");
                }
                return;
            }
            if (streamEnded) throw new ProtocolException("record after STREAM_END");
            switch (record.type) {
                case TYPE_BEGIN:
                    if (activeClipId != 0L) {
                        throw new ProtocolException(
                                "BEGIN while clip " + activeClipId + " is active");
                    }
                    activeClipId = record.clipId;
                    expectedSequence = 0;
                    activeClipBytes = 0L;
                    return;
                case TYPE_PCM:
                    requireActiveClip(record, "PCM");
                    if (record.sequence != expectedSequence) {
                        throw new ProtocolException(
                                "PCM sequence " + record.sequence
                                        + " != expected " + expectedSequence);
                    }
                    activeClipBytes += record.pcm.length;
                    if (activeClipBytes > MAX_CLIP_PCM_BYTES) {
                        throw new ProtocolException(
                                "clip PCM exceeds " + MAX_CLIP_PCM_BYTES + " bytes");
                    }
                    expectedSequence++;
                    return;
                case TYPE_END:
                    requireActiveClip(record, "END");
                    if (record.sequence != expectedSequence) {
                        throw new ProtocolException(
                                "END next sequence " + record.sequence
                                        + " != expected " + expectedSequence);
                    }
                    clearClip();
                    return;
                case TYPE_ABORT:
                    requireActiveClip(record, "ABORT");
                    clearClip();
                    return;
                case TYPE_STREAM_END:
                    if (activeClipId != 0L) {
                        throw new ProtocolException(
                                "STREAM_END while clip " + activeClipId + " is active");
                    }
                    streamEnded = true;
                    return;
                default:
                    throw new ProtocolException("unknown validated record type " + record.type);
            }
        }

        public boolean hasActiveClip() {
            return activeClipId != 0L;
        }

        public long activeClipId() {
            return activeClipId;
        }

        public int expectedSequence() {
            return expectedSequence;
        }

        public boolean isStreamEnded() {
            return streamEnded;
        }

        private void requireActiveClip(Record record, String context)
                throws ProtocolException {
            if (activeClipId == 0L) {
                throw new ProtocolException(context + " without an active clip");
            }
            if (record.clipId != activeClipId) {
                throw new ProtocolException(
                        context + " clipId " + record.clipId
                                + " != active " + activeClipId);
            }
        }

        private void clearClip() {
            activeClipId = 0L;
            expectedSequence = 0;
            activeClipBytes = 0L;
        }
    }

    public static final class ProtocolException extends IOException {
        public ProtocolException(String message) {
            super(message);
        }

        public ProtocolException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
