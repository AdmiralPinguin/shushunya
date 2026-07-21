package com.shushunya.m.wear.audio;

import android.content.Context;
import android.content.SharedPreferences;

/** Device-protected pin for the one phone node allowed to open the private TTS sink. */
public final class PrivateTtsTargetStore {
    private static final String PREFS = "private_tts_phone_target_v1";
    private static final String KEY_NODE_ID = "node_id";
    private static final int MAX_NODE_ID_CHARS = 256;

    private PrivateTtsTargetStore() {}

    public static boolean remember(Context context, String nodeId) {
        String clean = clean(nodeId);
        if (clean.isEmpty()) return false;
        return prefs(context).edit().putString(KEY_NODE_ID, clean).commit();
    }

    public static String read(Context context) {
        return clean(prefs(context).getString(KEY_NODE_ID, ""));
    }

    /** Accepts the pinned phone, or atomically pins the first authenticated state source. */
    public static synchronized boolean acceptOrRemember(Context context, String sourceNodeId) {
        String clean = clean(sourceNodeId);
        if (clean.isEmpty()) return false;
        String stored = read(context);
        if (!stored.isEmpty()) return stored.equals(clean);
        return remember(context, clean);
    }

    public static boolean matches(Context context, String sourceNodeId) {
        String stored = read(context);
        String source = clean(sourceNodeId);
        return !stored.isEmpty() && stored.equals(source);
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
