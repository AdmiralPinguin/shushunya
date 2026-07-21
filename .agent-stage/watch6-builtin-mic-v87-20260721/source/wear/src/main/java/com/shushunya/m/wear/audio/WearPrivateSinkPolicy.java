package com.shushunya.m.wear.audio;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Pure fail-closed selector for the one allowed SoundForm output. */
public final class WearPrivateSinkPolicy {
    public static final String KNOWN_SOUNDFORM_ADDRESS = "43:45:00:01:55:12";
    public static final int TYPE_BLUETOOTH_A2DP = 8;
    public static final int TYPE_BLE_HEADSET = 26;

    public enum Error {
        NONE,
        NO_SOUNDFORM,
        AMBIGUOUS_SOUNDFORM
    }

    public static final class Candidate {
        public final int type;
        public final String address;
        public final String product;
        public final int runtimeId;

        public Candidate(int type, String address, String product, int runtimeId) {
            this.type = type;
            this.address = normalizeAddress(address);
            this.product = normalizeProduct(product);
            this.runtimeId = runtimeId;
        }

        public boolean isAllowedSoundForm() {
            return isSupportedType(type)
                    && KNOWN_SOUNDFORM_ADDRESS.equals(address)
                    && product.contains("soundform");
        }

        public boolean sameFingerprint(Binding binding) {
            return binding != null
                    && type == binding.type
                    && address.equals(binding.address)
                    && product.equals(binding.product);
        }
    }

    public static final class Binding {
        public final int type;
        public final String address;
        public final String product;
        public final int runtimeId;
        public final boolean rebindUsed;

        public Binding(
                int type,
                String address,
                String product,
                int runtimeId,
                boolean rebindUsed) {
            this.type = type;
            this.address = normalizeAddress(address);
            this.product = normalizeProduct(product);
            this.runtimeId = runtimeId;
            this.rebindUsed = rebindUsed;
        }
    }

    public static final class Selection {
        public final Candidate candidate;
        public final Error error;
        public final boolean consumedRebind;

        private Selection(Candidate candidate, Error error, boolean consumedRebind) {
            this.candidate = candidate;
            this.error = error;
            this.consumedRebind = consumedRebind;
        }

        public boolean hasTarget() {
            return candidate != null && error == Error.NONE;
        }
    }

    private WearPrivateSinkPolicy() {}

    public static Selection select(List<Candidate> candidates, Binding persisted) {
        List<Candidate> eligible = new ArrayList<>();
        if (candidates != null) {
            for (Candidate candidate : candidates) {
                if (candidate != null && candidate.isAllowedSoundForm()) eligible.add(candidate);
            }
        }
        if (eligible.isEmpty()) return new Selection(null, Error.NO_SOUNDFORM, false);
        if (eligible.size() != 1) {
            return new Selection(null, Error.AMBIGUOUS_SOUNDFORM, false);
        }

        Candidate only = eligible.get(0);
        if (persisted == null || only.sameFingerprint(persisted)) {
            return new Selection(only, Error.NONE, false);
        }
        if (!persisted.rebindUsed) {
            return new Selection(only, Error.NONE, true);
        }
        return new Selection(null, Error.NO_SOUNDFORM, false);
    }

    public static boolean isSupportedType(int type) {
        return type == TYPE_BLUETOOTH_A2DP || type == TYPE_BLE_HEADSET;
    }

    static String normalizeAddress(String value) {
        return value == null ? "" : value.trim().toUpperCase(Locale.ROOT);
    }

    static String normalizeProduct(String value) {
        return value == null
                ? ""
                : value.trim().toLowerCase(Locale.ROOT).replaceAll("\\s+", " ");
    }
}
