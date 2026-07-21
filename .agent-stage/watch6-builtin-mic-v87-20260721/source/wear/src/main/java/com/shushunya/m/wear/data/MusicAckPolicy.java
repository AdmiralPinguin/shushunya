package com.shushunya.m.wear.data;

import java.util.Locale;

/** Exact semantic terminal acknowledgement policy for standalone MUSIC. */
final class MusicAckPolicy {
    private MusicAckPolicy() {}

    static boolean accepts(
            String expectedRequestId,
            String expectedPhoneNodeId,
            String responseRequestId,
            String sourceNodeId,
            String musicState,
            boolean exactErrorField) {
        return exactErrorField
                && sameNonEmpty(expectedRequestId, responseRequestId)
                && sameNonEmpty(expectedPhoneNodeId, sourceNodeId)
                && isSemanticState(musicState);
    }

    static boolean isSemanticState(String value) {
        switch (clean(value).toLowerCase(Locale.ROOT)) {
            case "playing":
            case "paused":
            case "unavailable":
                return true;
            default:
                return false;
        }
    }

    private static boolean sameNonEmpty(String expected, String actual) {
        return expected != null
                && !expected.isEmpty()
                && expected.equals(actual);
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
