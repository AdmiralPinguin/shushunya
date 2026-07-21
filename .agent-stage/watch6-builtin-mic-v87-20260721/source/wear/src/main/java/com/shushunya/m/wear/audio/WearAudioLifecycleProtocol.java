package com.shushunya.m.wear.audio;

/** Host-testable JSON/path contract for one exact Watch PCM lifecycle. */
public final class WearAudioLifecycleProtocol {
    public static final int VERSION = 1;
    public static final String PATH_BINDING = "/shushunya/audio/watch/binding/v1";
    public static final String PATH_DRAIN = "/shushunya/audio/watch/drain/v1";
    public static final String PATH_TERMINAL = "/shushunya/audio/watch/terminal/v1";
    public static final String PATH_TERMINAL_ACK =
            "/shushunya/audio/watch/terminal-ack/v1";
    /** Failure before a proven non-zero frame/channel exists. */
    public static final String PATH_STARTUP_FAILURE =
            "/shushunya/audio/watch/startup-failure/v1";
    public static final String PATH_STARTUP_FAILURE_ACK =
            "/shushunya/audio/watch/startup-failure-ack/v1";

    public static final String DISPOSITION_GRACEFUL_EOS = "graceful_eos";
    public static final String DISPOSITION_HARD_FAILURE = "hard_failure";
    public static final String ACK_FINISHED = "finished";
    public static final String ACK_ABORTED = "aborted";

    public static final long MAX_UNSIGNED_SEQUENCE = 0xffff_ffffL;
    public static final long MAX_DRAIN_TIMEOUT_MS = 8_000L;
    public static final int MAX_MESSAGE_BYTES = 16_384;
    public static final int MAX_DETAIL_CHARS = 2_048;

    private WearAudioLifecycleProtocol() {}

    public static String bindingJson(
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId) {
        if (!validId(requestId) || !validId(captureGroupId)
                || runGeneration <= 0L || sessionId <= 0L) return "";
        String payload = "{\"version\":" + VERSION
                + ",\"requestId\":\"" + requestId + "\""
                + ",\"captureGroupId\":\"" + captureGroupId + "\""
                + ",\"runGeneration\":" + runGeneration
                + ",\"sessionId\":" + sessionId + "}";
        return fitsMessage(payload) ? payload : "";
    }

    public static String drainJson(
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            long timeoutMs) {
        if (!validId(requestId) || !validId(captureGroupId)
                || runGeneration <= 0L
                || sessionId < 0L
                || timeoutMs <= 0L
                || timeoutMs > MAX_DRAIN_TIMEOUT_MS) return "";
        String payload = "{\"version\":" + VERSION
                + ",\"requestId\":\"" + requestId + "\""
                + ",\"captureGroupId\":\"" + captureGroupId + "\""
                + ",\"runGeneration\":" + runGeneration
                + ",\"sessionId\":" + sessionId
                + ",\"timeoutMs\":" + timeoutMs + "}";
        return fitsMessage(payload) ? payload : "";
    }

    public static String terminalJson(
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            String disposition,
            long lastSequence,
            long droppedFrames,
            String detail) {
        if (!validId(requestId) || !validId(captureGroupId)
                || runGeneration <= 0L || sessionId <= 0L
                || (!DISPOSITION_GRACEFUL_EOS.equals(disposition)
                && !DISPOSITION_HARD_FAILURE.equals(disposition))
                || lastSequence < -1L || lastSequence > MAX_UNSIGNED_SEQUENCE
                || droppedFrames < 0L) return "";
        String payload = "{\"version\":" + VERSION
                + ",\"requestId\":\"" + requestId + "\""
                + ",\"captureGroupId\":\"" + captureGroupId + "\""
                + ",\"runGeneration\":" + runGeneration
                + ",\"sessionId\":" + sessionId
                + ",\"disposition\":\"" + disposition + "\""
                + ",\"lastSequence\":" + lastSequence
                + ",\"droppedFrames\":" + droppedFrames
                + ",\"detail\":\"" + escape(detail) + "\"}";
        return fitsMessage(payload) ? payload : "";
    }

    public static String terminalAckJson(
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            String disposition,
            long acceptedLastSequence,
            String error,
            long ackAtMs) {
        if (!validId(requestId) || !validId(captureGroupId)
                || runGeneration <= 0L || sessionId <= 0L
                || (!ACK_FINISHED.equals(disposition) && !ACK_ABORTED.equals(disposition))
                || acceptedLastSequence < -1L
                || acceptedLastSequence > MAX_UNSIGNED_SEQUENCE
                || ackAtMs <= 0L) return "";
        String payload = "{\"version\":" + VERSION
                + ",\"requestId\":\"" + requestId + "\""
                + ",\"captureGroupId\":\"" + captureGroupId + "\""
                + ",\"runGeneration\":" + runGeneration
                + ",\"sessionId\":" + sessionId
                + ",\"disposition\":\"" + disposition + "\""
                + ",\"acceptedLastSequence\":" + acceptedLastSequence
                + ",\"error\":\"" + escape(error) + "\""
                + ",\"ackAtMs\":" + ackAtMs + "}";
        return fitsMessage(payload) ? payload : "";
    }

    public static String startupFailureJson(
            String requestId,
            String code,
            String detail,
            long failedAtMs) {
        String exactCode = code == null ? "" : code.trim();
        if (!validId(requestId)
                || !exactCode.matches("[A-Z0-9_]{1,64}")
                || failedAtMs <= 0L) return "";
        String payload = "{\"version\":" + VERSION
                + ",\"requestId\":\"" + requestId.trim() + "\""
                + ",\"code\":\"" + exactCode + "\""
                + ",\"detail\":\"" + escape(detail) + "\""
                + ",\"failedAtMs\":" + failedAtMs + "}";
        return fitsMessage(payload) ? payload : "";
    }

    public static String startupFailureAckJson(
            String requestId,
            String code,
            long failedAtMs,
            long ackAtMs) {
        String exactCode = code == null ? "" : code.trim();
        if (!validId(requestId)
                || !exactCode.matches("[A-Z0-9_]{1,64}")
                || failedAtMs <= 0L
                || ackAtMs <= 0L) return "";
        String payload = "{\"version\":" + VERSION
                + ",\"requestId\":\"" + requestId.trim() + "\""
                + ",\"code\":\"" + exactCode + "\""
                + ",\"failedAtMs\":" + failedAtMs
                + ",\"ackAtMs\":" + ackAtMs + "}";
        return fitsMessage(payload) ? payload : "";
    }

    public static boolean validId(String value) {
        String clean = value == null ? "" : value.trim();
        return clean.matches("[A-Za-z0-9._:-]{1,160}");
    }

    private static String escape(String value) {
        String input = value == null ? "" : value;
        StringBuilder output = new StringBuilder(Math.min(4096, input.length() + 16));
        for (int index = 0; index < input.length() && index < MAX_DETAIL_CHARS; index++) {
            char character = input.charAt(index);
            switch (character) {
                case '\\': output.append("\\\\"); break;
                case '"': output.append("\\\""); break;
                case '\n': output.append("\\n"); break;
                case '\r': output.append("\\r"); break;
                case '\t': output.append("\\t"); break;
                default:
                    if (character >= 0x20) output.append(character);
                    break;
            }
        }
        return output.toString();
    }

    private static boolean fitsMessage(String payload) {
        return payload != null
                && payload.getBytes(java.nio.charset.StandardCharsets.UTF_8).length
                <= MAX_MESSAGE_BYTES;
    }
}
