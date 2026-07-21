package com.shushunya.m.wear.audio;

/** Pure decision helper for AudioManager's raw-input capability property. */
final class RawAudioSourcePolicy {
    private RawAudioSourcePolicy() {}

    static boolean supportsUnprocessed(String propertyValue) {
        return propertyValue != null && "true".equalsIgnoreCase(propertyValue.trim());
    }
}
