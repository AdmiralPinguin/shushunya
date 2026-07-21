package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

public final class WearPrivateSinkPolicyTest {
    private static WearPrivateSinkPolicy.Candidate soundForm(int id) {
        return new WearPrivateSinkPolicy.Candidate(
                WearPrivateSinkPolicy.TYPE_BLUETOOTH_A2DP,
                "43:45:00:01:55:12",
                "soundForm Motion",
                id);
    }

    @Test
    public void selectsOnlyExactKnownSoundForm() {
        WearPrivateSinkPolicy.Selection selection = WearPrivateSinkPolicy.select(
                Arrays.asList(
                        new WearPrivateSinkPolicy.Candidate(2, "", "Speaker", 1),
                        new WearPrivateSinkPolicy.Candidate(
                                WearPrivateSinkPolicy.TYPE_BLUETOOTH_A2DP,
                                "00:11:22:33:44:55",
                                "SoundForm",
                                2),
                        soundForm(3)),
                null);

        assertTrue(selection.hasTarget());
        assertEquals(3, selection.candidate.runtimeId);
    }

    @Test
    public void rejectsNoTargetAndAmbiguity() {
        WearPrivateSinkPolicy.Selection none = WearPrivateSinkPolicy.select(
                Collections.singletonList(new WearPrivateSinkPolicy.Candidate(
                        2, "", "watch speaker", 1)), null);
        assertFalse(none.hasTarget());
        assertEquals(WearPrivateSinkPolicy.Error.NO_SOUNDFORM, none.error);

        WearPrivateSinkPolicy.Selection two = WearPrivateSinkPolicy.select(
                Arrays.asList(soundForm(3), soundForm(4)), null);
        assertFalse(two.hasTarget());
        assertEquals(WearPrivateSinkPolicy.Error.AMBIGUOUS_SOUNDFORM, two.error);
    }

    @Test
    public void runtimeIdMayChangeWithoutConsumingRebind() {
        WearPrivateSinkPolicy.Binding binding = new WearPrivateSinkPolicy.Binding(
                WearPrivateSinkPolicy.TYPE_BLUETOOTH_A2DP,
                WearPrivateSinkPolicy.KNOWN_SOUNDFORM_ADDRESS,
                "soundform motion",
                4,
                false);
        WearPrivateSinkPolicy.Selection selection = WearPrivateSinkPolicy.select(
                Collections.singletonList(soundForm(99)), binding);

        assertTrue(selection.hasTarget());
        assertFalse(selection.consumedRebind);
        assertEquals(99, selection.candidate.runtimeId);
    }

    @Test
    public void fingerprintMigrationIsAllowedOnlyOnce() {
        WearPrivateSinkPolicy.Binding old = new WearPrivateSinkPolicy.Binding(
                WearPrivateSinkPolicy.TYPE_BLE_HEADSET,
                WearPrivateSinkPolicy.KNOWN_SOUNDFORM_ADDRESS,
                "soundform old",
                4,
                false);
        WearPrivateSinkPolicy.Selection first = WearPrivateSinkPolicy.select(
                Collections.singletonList(soundForm(9)), old);
        assertTrue(first.hasTarget());
        assertTrue(first.consumedRebind);

        WearPrivateSinkPolicy.Binding spent = new WearPrivateSinkPolicy.Binding(
                old.type, old.address, old.product, old.runtimeId, true);
        WearPrivateSinkPolicy.Selection second = WearPrivateSinkPolicy.select(
                Collections.singletonList(soundForm(9)), spent);
        assertFalse(second.hasTarget());
    }
}
