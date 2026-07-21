package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class RawAudioSourcePolicyTest {
    @Test
    public void selectsUnprocessedOnlyWhenPlatformExplicitlyAdvertisesIt() {
        assertTrue(RawAudioSourcePolicy.supportsUnprocessed("true"));
        assertTrue(RawAudioSourcePolicy.supportsUnprocessed(" TRUE "));
        assertFalse(RawAudioSourcePolicy.supportsUnprocessed(null));
        assertFalse(RawAudioSourcePolicy.supportsUnprocessed(""));
        assertFalse(RawAudioSourcePolicy.supportsUnprocessed("false"));
        assertFalse(RawAudioSourcePolicy.supportsUnprocessed("1"));
    }
}
