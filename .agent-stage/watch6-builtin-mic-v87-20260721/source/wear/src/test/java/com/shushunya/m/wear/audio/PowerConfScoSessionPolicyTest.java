package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.List;

public final class PowerConfScoSessionPolicyTest {
    private static PowerConfScoSessionPolicy.Device device(
            String name, String address, int bondState, boolean audioConnected) {
        return new PowerConfScoSessionPolicy.Device(
                name, address, bondState, audioConnected);
    }

    @Test
    public void acceptsOnlyThePinnedBondedPowerConfBeforeScoStarts() {
        PowerConfScoSessionPolicy.Selection selection =
                PowerConfScoSessionPolicy.selectForStart(
                        List.of(device(
                                "Anker PowerConf",
                                PowerConfScoSessionPolicy.EXPECTED_ADDRESS,
                                PowerConfScoSessionPolicy.BOND_BONDED,
                                false)),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS);

        assertTrue(selection.isReady());
        assertEquals(PowerConfScoSessionPolicy.SelectionDecision.READY,
                selection.decision);
    }

    @Test
    public void rejectsProductAliasesWrongAddressAndUnbondedDevice() {
        assertFalse(PowerConfScoSessionPolicy.isExactProduct("Anker PowerConf S3"));
        assertFalse(PowerConfScoSessionPolicy.isExactProduct("PowerConf"));
        assertEquals(PowerConfScoSessionPolicy.SelectionDecision.NO_EXACT_POWERCONF,
                PowerConfScoSessionPolicy.selectForStart(
                        List.of(device(
                                "Anker PowerConf S3",
                                PowerConfScoSessionPolicy.EXPECTED_ADDRESS,
                                PowerConfScoSessionPolicy.BOND_BONDED,
                                false)),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS).decision);
        assertEquals(PowerConfScoSessionPolicy.SelectionDecision.EXPECTED_ADDRESS_MISMATCH,
                PowerConfScoSessionPolicy.selectForStart(
                        List.of(device(
                                "Anker PowerConf",
                                "00:7F:1D:2E:C6:7F",
                                PowerConfScoSessionPolicy.BOND_BONDED,
                                false)),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS).decision);
        assertEquals(PowerConfScoSessionPolicy.SelectionDecision.POWERCONF_NOT_BONDED,
                PowerConfScoSessionPolicy.selectForStart(
                        List.of(device(
                                "Anker PowerConf",
                                PowerConfScoSessionPolicy.EXPECTED_ADDRESS,
                                10,
                                false)),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS).decision);
    }

    @Test
    public void refusesToStealOrShareExistingHfpAudio() {
        PowerConfScoSessionPolicy.Device powerConf = device(
                "Anker PowerConf",
                PowerConfScoSessionPolicy.EXPECTED_ADDRESS,
                PowerConfScoSessionPolicy.BOND_BONDED,
                false);
        PowerConfScoSessionPolicy.Device other = device(
                "SoundForm",
                "43:45:00:01:55:12",
                PowerConfScoSessionPolicy.BOND_BONDED,
                true);
        assertEquals(PowerConfScoSessionPolicy.SelectionDecision.OTHER_AUDIO_CONNECTED,
                PowerConfScoSessionPolicy.selectForStart(
                        List.of(powerConf, other),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS).decision);

        PowerConfScoSessionPolicy.Device activePowerConf = device(
                "Anker PowerConf",
                PowerConfScoSessionPolicy.EXPECTED_ADDRESS,
                PowerConfScoSessionPolicy.BOND_BONDED,
                true);
        assertEquals(
                PowerConfScoSessionPolicy.AudioDecision.EXCLUSIVE_POWERCONF_AUDIO,
                PowerConfScoSessionPolicy.evaluateAudio(
                        List.of(activePowerConf),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS).decision);
        assertEquals(PowerConfScoSessionPolicy.AudioDecision.OTHER_AUDIO_CONNECTED,
                PowerConfScoSessionPolicy.evaluateAudio(
                        List.of(activePowerConf, other),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS).decision);
    }
}
