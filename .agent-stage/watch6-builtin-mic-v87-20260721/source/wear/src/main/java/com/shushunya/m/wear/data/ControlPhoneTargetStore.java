package com.shushunya.m.wear.data;

import android.content.Context;
import android.content.SharedPreferences;

/** Device-protected pin for the one authenticated phone control/audio peer. */
final class ControlPhoneTargetStore {
    private static final String PREFS = "shushunya_control_phone_target_v1";
    private static final String KEY_NODE_ID = "node_id";
    private static final int MAX_NODE_ID_CHARS = 256;

    private ControlPhoneTargetStore() {}

    static synchronized boolean acceptOrRemember(Context context, String sourceNodeId) {
        String source = clean(sourceNodeId);
        if (source.isEmpty()) return false;
        String stored = read(context);
        if (!stored.isEmpty()) return stored.equals(source);
        return remember(context, source);
    }

    static synchronized boolean rememberExact(Context context, String sourceNodeId) {
        String source = clean(sourceNodeId);
        if (source.isEmpty()) return false;
        String stored = read(context);
        if (!stored.isEmpty() && !stored.equals(source)) return false;
        return remember(context, source);
    }

    /** Returns the exact durable control peer without performing discovery. */
    static synchronized String selectedNodeId(Context context) {
        return read(context);
    }

    /** Checks an existing pin without letting an uncorrelated ACK create one. */
    static synchronized boolean acceptsExistingOrEmpty(Context context, String sourceNodeId) {
        String source = clean(sourceNodeId);
        if (source.isEmpty()) return false;
        String stored = read(context);
        return stored.isEmpty() || stored.equals(source);
    }

    private static boolean remember(Context context, String nodeId) {
        return prefs(context).edit().putString(KEY_NODE_ID, nodeId).commit();
    }

    private static String read(Context context) {
        return clean(prefs(context).getString(KEY_NODE_ID, ""));
    }

    private static SharedPreferences prefs(Context context) {
        Context app = context.getApplicationContext();
        Context storage = app.createDeviceProtectedStorageContext();
        return storage.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String clean(String value) {
        if (value == null) return "";
        String clean = value.trim();
        return clean.length() <= MAX_NODE_ID_CHARS ? clean : "";
    }
}
