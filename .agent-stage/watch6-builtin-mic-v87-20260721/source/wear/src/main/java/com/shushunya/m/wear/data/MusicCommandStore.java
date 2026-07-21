package com.shushunya.m.wear.data;

import android.content.Context;
import android.content.SharedPreferences;

/** Device-protected single-slot outbox for one standalone MUSIC command. */
final class MusicCommandStore {
    private static final String PREFS = "shushunya_music_command_v1";
    private static final String KEY_REQUEST_ID = "request_id";
    private static final String KEY_ISSUED_AT = "issued_at";
    private static final String KEY_PAYLOAD = "payload";
    private static final String KEY_STARTED_AT = "started_at";
    private static final String KEY_DEADLINE_AT = "deadline_at";
    private static final String KEY_PHONE_NODE = "phone_node";
    private static final String KEY_NEXT_ATTEMPT = "next_attempt";

    private MusicCommandStore() {}

    static synchronized boolean replace(Context context, MusicCommandState state) {
        if (state == null || !state.isValid()) return false;
        return write(prefs(context).edit().clear(), state).commit();
    }

    static synchronized MusicCommandState read(Context context) {
        SharedPreferences preferences = prefs(context);
        MusicCommandState state = MusicCommandState.restore(
                preferences.getString(KEY_REQUEST_ID, ""),
                preferences.getLong(KEY_ISSUED_AT, 0L),
                preferences.getString(KEY_PAYLOAD, ""),
                preferences.getLong(KEY_STARTED_AT, 0L),
                preferences.getLong(KEY_DEADLINE_AT, 0L),
                preferences.getString(KEY_PHONE_NODE, ""),
                preferences.getInt(KEY_NEXT_ATTEMPT, -1));
        if (state == null && preferences.contains(KEY_REQUEST_ID)) {
            preferences.edit().clear().commit();
        }
        return state;
    }

    static synchronized MusicCommandState bindPhoneNode(
            Context context,
            String requestId,
            String phoneNodeId) {
        MusicCommandState current = read(context);
        if (current == null || !current.requestId.equals(clean(requestId))) return null;
        String node = clean(phoneNodeId);
        if (node.isEmpty()
                || (!current.phoneNodeId.isEmpty()
                && !current.phoneNodeId.equals(node))) return null;
        MusicCommandState updated = current.withPhoneNode(node);
        if (updated == null || !write(prefs(context).edit(), updated).commit()) return null;
        return updated;
    }

    /** Commits the next index before the asynchronous send, avoiding replay storms. */
    static synchronized MusicCommandState claimAttempt(
            Context context,
            String requestId,
            int attemptIndex) {
        MusicCommandState current = read(context);
        if (current == null
                || !current.requestId.equals(clean(requestId))
                || current.nextAttemptIndex != attemptIndex) return null;
        MusicCommandState updated = current.withNextAttempt(attemptIndex + 1);
        if (updated == null || !write(prefs(context).edit(), updated).commit()) return null;
        return current;
    }

    static synchronized boolean clearExact(Context context, String requestId) {
        MusicCommandState current = read(context);
        if (current == null || !current.requestId.equals(clean(requestId))) return false;
        return prefs(context).edit().clear().commit();
    }

    private static SharedPreferences.Editor write(
            SharedPreferences.Editor editor,
            MusicCommandState state) {
        return editor.putString(KEY_REQUEST_ID, state.requestId)
                .putLong(KEY_ISSUED_AT, state.issuedAtMs)
                .putString(KEY_PAYLOAD, state.jsonPayload)
                .putLong(KEY_STARTED_AT, state.startedAtMs)
                .putLong(KEY_DEADLINE_AT, state.deadlineAtMs)
                .putString(KEY_PHONE_NODE, state.phoneNodeId)
                .putInt(KEY_NEXT_ATTEMPT, state.nextAttemptIndex);
    }

    private static SharedPreferences prefs(Context context) {
        Context app = context.getApplicationContext();
        Context storage = app.createDeviceProtectedStorageContext();
        return storage.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
