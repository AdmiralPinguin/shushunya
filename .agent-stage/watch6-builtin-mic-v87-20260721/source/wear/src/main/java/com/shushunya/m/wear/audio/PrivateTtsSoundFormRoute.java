package com.shushunya.m.wear.audio;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Android adapter around the pure exact-SoundForm selection policy. */
final class PrivateTtsSoundFormRoute {
    private static final String PREFS = "private_tts_soundform_route_v1";
    private static final String KEY_TYPE = "type";
    private static final String KEY_ADDRESS = "address";
    private static final String KEY_PRODUCT = "product";
    private static final String KEY_RUNTIME_ID = "runtime_id";
    private static final String KEY_BOUND = "bound";
    private static final String KEY_REBIND_USED = "rebind_used";

    static final class Result {
        final AudioDeviceInfo device;
        final String error;

        Result(AudioDeviceInfo device, String error) {
            this.device = device;
            this.error = error;
        }

        boolean hasDevice() {
            return device != null && error.isEmpty();
        }
    }

    private PrivateTtsSoundFormRoute() {}

    static Result resolve(Context context) {
        AudioManager manager = context.getSystemService(AudioManager.class);
        if (manager == null) return new Result(null, "NO_SOUNDFORM audio_manager");

        AudioDeviceInfo[] outputs = manager.getDevices(AudioManager.GET_DEVICES_OUTPUTS);
        List<WearPrivateSinkPolicy.Candidate> candidates = new ArrayList<>();
        Map<Integer, AudioDeviceInfo> byId = new HashMap<>();
        for (AudioDeviceInfo device : outputs) {
            if (device == null || !WearPrivateSinkPolicy.isSupportedType(device.getType())) {
                continue;
            }
            WearPrivateSinkPolicy.Candidate candidate = candidate(device);
            candidates.add(candidate);
            byId.put(candidate.runtimeId, device);
        }

        SharedPreferences preferences = prefs(context);
        WearPrivateSinkPolicy.Binding persisted = readBinding(preferences);
        WearPrivateSinkPolicy.Selection selection =
                WearPrivateSinkPolicy.select(candidates, persisted);
        if (!selection.hasTarget()) {
            return new Result(null, selection.error.name());
        }

        AudioDeviceInfo selected = byId.get(selection.candidate.runtimeId);
        if (selected == null) return new Result(null, "NO_SOUNDFORM disappeared");
        boolean rebindUsed = (persisted != null && persisted.rebindUsed)
                || selection.consumedRebind;
        if (!saveBinding(preferences, selection.candidate, rebindUsed)) {
            return new Result(null, "ROUTE_REJECTED persist");
        }
        return new Result(selected, "");
    }

    static boolean exactMatch(AudioDeviceInfo expected, AudioDeviceInfo actual) {
        if (expected == null || actual == null) return false;
        WearPrivateSinkPolicy.Candidate left = candidate(expected);
        WearPrivateSinkPolicy.Candidate right = candidate(actual);
        return left.runtimeId == right.runtimeId
                && left.type == right.type
                && left.address.equals(right.address)
                && left.product.equals(right.product)
                && left.isAllowedSoundForm()
                && right.isAllowedSoundForm();
    }

    static String describe(AudioDeviceInfo device) {
        if (device == null) return "none";
        WearPrivateSinkPolicy.Candidate candidate = candidate(device);
        return candidate.product + "/" + candidate.address
                + "/type=" + candidate.type + "/id=" + candidate.runtimeId;
    }

    private static WearPrivateSinkPolicy.Candidate candidate(AudioDeviceInfo device) {
        CharSequence product = device.getProductName();
        return new WearPrivateSinkPolicy.Candidate(
                device.getType(),
                device.getAddress(),
                product == null ? "" : product.toString(),
                device.getId());
    }

    private static WearPrivateSinkPolicy.Binding readBinding(
            SharedPreferences preferences) {
        if (!preferences.getBoolean(KEY_BOUND, false)) return null;
        return new WearPrivateSinkPolicy.Binding(
                preferences.getInt(KEY_TYPE, -1),
                preferences.getString(KEY_ADDRESS, ""),
                preferences.getString(KEY_PRODUCT, ""),
                preferences.getInt(KEY_RUNTIME_ID, -1),
                preferences.getBoolean(KEY_REBIND_USED, false));
    }

    private static boolean saveBinding(
            SharedPreferences preferences,
            WearPrivateSinkPolicy.Candidate candidate,
            boolean rebindUsed) {
        return preferences.edit()
                .putBoolean(KEY_BOUND, true)
                .putInt(KEY_TYPE, candidate.type)
                .putString(KEY_ADDRESS, candidate.address)
                .putString(KEY_PRODUCT, candidate.product)
                .putInt(KEY_RUNTIME_ID, candidate.runtimeId)
                .putBoolean(KEY_REBIND_USED, rebindUsed)
                .commit();
    }

    private static SharedPreferences prefs(Context context) {
        Context app = context.getApplicationContext();
        return app.createDeviceProtectedStorageContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
