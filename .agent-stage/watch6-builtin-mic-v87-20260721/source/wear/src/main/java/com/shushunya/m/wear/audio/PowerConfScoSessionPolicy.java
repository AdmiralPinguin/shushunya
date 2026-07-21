package com.shushunya.m.wear.audio;

import java.util.Collections;
import java.util.List;
import java.util.Locale;

/** Pure fail-closed policy for the one physical PowerConf used by Shushunya. */
public final class PowerConfScoSessionPolicy {
    public static final int BOND_BONDED = 12;
    public static final String EXPECTED_NAME = "Anker PowerConf";
    public static final String EXPECTED_ADDRESS = "00:7F:1D:2E:C6:7E";

    private PowerConfScoSessionPolicy() {}

    public enum SelectionDecision {
        READY,
        NONE_CONNECTED,
        NO_EXACT_POWERCONF,
        MULTIPLE_EXACT_POWERCONF,
        POWERCONF_NOT_BONDED,
        POWERCONF_INVALID_ADDRESS,
        EXPECTED_ADDRESS_MISMATCH,
        POWERCONF_AUDIO_ALREADY_CONNECTED,
        OTHER_AUDIO_CONNECTED
    }

    public enum AudioDecision {
        EXCLUSIVE_POWERCONF_AUDIO,
        POWERCONF_MISSING,
        DUPLICATE_POWERCONF_ADDRESS,
        POWERCONF_AUDIO_NOT_CONNECTED,
        OTHER_AUDIO_CONNECTED
    }

    public static Selection selectForStart(
            List<Device> connectedDevices, String expectedAddress) {
        List<Device> devices = connectedDevices == null
                ? Collections.emptyList() : connectedDevices;
        if (devices.isEmpty()) {
            return new Selection(SelectionDecision.NONE_CONNECTED, null);
        }

        Device selected = null;
        int exactProductCount = 0;
        for (Device device : devices) {
            if (device == null || !isExactProduct(device.name)) continue;
            exactProductCount++;
            selected = device;
        }
        if (exactProductCount == 0) {
            return new Selection(SelectionDecision.NO_EXACT_POWERCONF, null);
        }
        if (exactProductCount != 1) {
            return new Selection(SelectionDecision.MULTIPLE_EXACT_POWERCONF, null);
        }
        if (selected.bondState != BOND_BONDED) {
            return new Selection(SelectionDecision.POWERCONF_NOT_BONDED, selected);
        }
        if (!isUsableHardwareAddress(selected.address)) {
            return new Selection(SelectionDecision.POWERCONF_INVALID_ADDRESS, selected);
        }

        String expected = clean(expectedAddress);
        if (!EXPECTED_ADDRESS.equalsIgnoreCase(expected)
                || !expected.equalsIgnoreCase(selected.address)) {
            return new Selection(SelectionDecision.EXPECTED_ADDRESS_MISMATCH, selected);
        }
        if (selected.audioConnected) {
            return new Selection(
                    SelectionDecision.POWERCONF_AUDIO_ALREADY_CONNECTED, selected);
        }
        for (Device device : devices) {
            if (device != null && device != selected && device.audioConnected) {
                return new Selection(SelectionDecision.OTHER_AUDIO_CONNECTED, selected);
            }
        }
        return new Selection(SelectionDecision.READY, selected);
    }

    /** Verifies that SCO belongs only to the already selected physical address. */
    public static AudioEvaluation evaluateAudio(
            List<Device> connectedDevices, String selectedAddress) {
        List<Device> devices = connectedDevices == null
                ? Collections.emptyList() : connectedDevices;
        String expected = clean(selectedAddress);
        Device selected = null;
        int exactMatches = 0;
        for (Device device : devices) {
            if (device == null || !expected.equalsIgnoreCase(device.address)) continue;
            exactMatches++;
            selected = device;
        }
        if (exactMatches == 0) {
            return new AudioEvaluation(AudioDecision.POWERCONF_MISSING, null);
        }
        if (exactMatches != 1) {
            return new AudioEvaluation(AudioDecision.DUPLICATE_POWERCONF_ADDRESS, null);
        }
        if (!isExactProduct(selected.name)) {
            return new AudioEvaluation(AudioDecision.POWERCONF_MISSING, null);
        }
        for (Device device : devices) {
            if (device != null && device != selected && device.audioConnected) {
                return new AudioEvaluation(AudioDecision.OTHER_AUDIO_CONNECTED, selected);
            }
        }
        if (!selected.audioConnected) {
            return new AudioEvaluation(AudioDecision.POWERCONF_AUDIO_NOT_CONNECTED, selected);
        }
        return new AudioEvaluation(AudioDecision.EXCLUSIVE_POWERCONF_AUDIO, selected);
    }

    public static boolean isExactProduct(String value) {
        return EXPECTED_NAME.toLowerCase(Locale.ROOT).equals(
                clean(value).toLowerCase(Locale.ROOT).replaceAll("\\s+", " "));
    }

    public static boolean isUsableHardwareAddress(String value) {
        String address = clean(value);
        if (!address.matches("(?i)[0-9a-f]{2}(:[0-9a-f]{2}){5}")) return false;
        return !"00:00:00:00:00:00".equalsIgnoreCase(address)
                && !"02:00:00:00:00:00".equalsIgnoreCase(address);
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    public static final class Device {
        public final String name;
        public final String address;
        public final int bondState;
        public final boolean audioConnected;

        public Device(String name, String address, int bondState, boolean audioConnected) {
            this.name = clean(name);
            this.address = clean(address);
            this.bondState = bondState;
            this.audioConnected = audioConnected;
        }
    }

    public static final class Selection {
        public final SelectionDecision decision;
        public final Device device;

        private Selection(SelectionDecision decision, Device device) {
            this.decision = decision;
            this.device = device;
        }

        public boolean isReady() {
            return decision == SelectionDecision.READY;
        }
    }

    public static final class AudioEvaluation {
        public final AudioDecision decision;
        public final Device device;

        private AudioEvaluation(AudioDecision decision, Device device) {
            this.decision = decision;
            this.device = device;
        }

        public boolean isExclusive() {
            return decision == AudioDecision.EXCLUSIVE_POWERCONF_AUDIO;
        }
    }
}
