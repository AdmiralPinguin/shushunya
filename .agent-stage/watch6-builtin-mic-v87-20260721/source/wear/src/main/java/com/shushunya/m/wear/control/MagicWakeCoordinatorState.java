package com.shushunya.m.wear.control;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

/** Pure, versioned durable state for one exact Watch-to-phone magic command. */
final class MagicWakeCoordinatorState {
    enum Phase { DISCOVERING, WAITING_ACCEPTED, AWAITING_TERMINAL }

    private static final String VERSION = "2";

    final String requestId;
    final long issuedAtMs;
    final String phoneNodeId;
    final long wakeStartedAtMs;
    final int nextAttemptIndex;
    final Phase phase;
    final boolean targetStart;
    final boolean acceptedActionConsumed;

    MagicWakeCoordinatorState(
            String requestId,
            long issuedAtMs,
            String phoneNodeId,
            long wakeStartedAtMs,
            int nextAttemptIndex,
            Phase phase,
            boolean targetStart,
            boolean acceptedActionConsumed) {
        this.requestId = clean(requestId, 256);
        this.issuedAtMs = issuedAtMs;
        this.phoneNodeId = clean(phoneNodeId, 256);
        this.wakeStartedAtMs = wakeStartedAtMs;
        this.nextAttemptIndex = Math.max(0, nextAttemptIndex);
        this.phase = phase == null ? Phase.DISCOVERING : phase;
        this.targetStart = targetStart;
        this.acceptedActionConsumed = acceptedActionConsumed;
    }

    static MagicWakeCoordinatorState discovering(String requestId, long issuedAtMs) {
        return new MagicWakeCoordinatorState(
                requestId, issuedAtMs, "", 0L, 0, Phase.DISCOVERING, false, false);
    }

    MagicWakeCoordinatorState withPhoneNode(String nodeId, long startedAtMs) {
        return new MagicWakeCoordinatorState(
                requestId,
                issuedAtMs,
                nodeId,
                startedAtMs,
                0,
                Phase.WAITING_ACCEPTED,
                false,
                false);
    }

    MagicWakeCoordinatorState withNextAttempt(int nextIndex) {
        return new MagicWakeCoordinatorState(
                requestId,
                issuedAtMs,
                phoneNodeId,
                wakeStartedAtMs,
                nextIndex,
                phase,
                targetStart,
                acceptedActionConsumed);
    }

    MagicWakeCoordinatorState accepted(boolean exactTargetStart) {
        return new MagicWakeCoordinatorState(
                requestId,
                issuedAtMs,
                phoneNodeId,
                wakeStartedAtMs,
                nextAttemptIndex,
                Phase.AWAITING_TERMINAL,
                exactTargetStart,
                false);
    }

    MagicWakeCoordinatorState acceptedActionConsumed() {
        return new MagicWakeCoordinatorState(
                requestId,
                issuedAtMs,
                phoneNodeId,
                wakeStartedAtMs,
                nextAttemptIndex,
                phase,
                targetStart,
                true);
    }

    boolean isAccepted() {
        return phase == Phase.AWAITING_TERMINAL;
    }

    boolean hasPendingAcceptedAction() {
        return isAccepted() && !acceptedActionConsumed;
    }

    boolean isValid() {
        if (requestId.isEmpty() || issuedAtMs <= 0L) return false;
        if (phase == Phase.DISCOVERING) {
            return phoneNodeId.isEmpty() && wakeStartedAtMs == 0L
                    && !targetStart && !acceptedActionConsumed;
        }
        if (phoneNodeId.isEmpty() || wakeStartedAtMs <= 0L) return false;
        return phase == Phase.AWAITING_TERMINAL || (!targetStart && !acceptedActionConsumed);
    }

    String encode() {
        if (!isValid()) return "";
        return VERSION
                + "|" + encodeText(requestId)
                + "|" + issuedAtMs
                + "|" + encodeText(phoneNodeId)
                + "|" + wakeStartedAtMs
                + "|" + nextAttemptIndex
                + "|" + phase.name()
                + "|" + targetStart
                + "|" + acceptedActionConsumed;
    }

    static MagicWakeCoordinatorState decode(String encoded) {
        if (encoded == null || encoded.isEmpty() || encoded.length() > 4_096) return null;
        try {
            String[] fields = encoded.split("\\|", -1);
            if (fields.length != 9 || !VERSION.equals(fields[0])) return null;
            MagicWakeCoordinatorState state = new MagicWakeCoordinatorState(
                    decodeText(fields[1]),
                    Long.parseLong(fields[2]),
                    decodeText(fields[3]),
                    Long.parseLong(fields[4]),
                    Integer.parseInt(fields[5]),
                    Phase.valueOf(fields[6]),
                    Boolean.parseBoolean(fields[7]),
                    Boolean.parseBoolean(fields[8]));
            return state.isValid() ? state : null;
        } catch (RuntimeException invalid) {
            return null;
        }
    }

    private static String encodeText(String value) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(
                value.getBytes(StandardCharsets.UTF_8));
    }

    private static String decodeText(String value) {
        return new String(Base64.getUrlDecoder().decode(value), StandardCharsets.UTF_8);
    }

    private static String clean(String value, int maxLength) {
        String clean = value == null ? "" : value.trim();
        return clean.length() > maxLength ? clean.substring(0, maxLength) : clean;
    }
}
