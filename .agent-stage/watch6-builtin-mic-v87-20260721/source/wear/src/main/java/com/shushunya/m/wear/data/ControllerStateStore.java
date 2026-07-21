package com.shushunya.m.wear.data;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.Locale;

public final class ControllerStateStore {
    public enum Kind { LIVE, MUSIC }
    public enum State { UNKNOWN, RUNNING, PAUSED, STOPPED, ERROR }
    public enum LivePhase { NONE, STARTING, STOPPING }

    private static final String PREFS = "wear_controller_state";
    private static final long PENDING_TIMEOUT_MS = 12_000L;
    private static final long TAP_DEBOUNCE_MS = 700L;
    private static final String MAGIC_SETTLE_REQUEST_ID = "magic_settle_request_id";
    private static final String MAGIC_SETTLE_UNTIL = "magic_settle_until";
    private static final String MAGIC_WAKE_COORDINATOR_STATE =
            "magic_wake_coordinator_state_v1";
    private static final Object MAGIC_SETTLE_LOCK = new Object();

    private ControllerStateStore() {}

    private static SharedPreferences prefs(Context context) {
        Context protectedContext = context.createDeviceProtectedStorageContext();
        return protectedContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String key(Kind kind, String suffix) {
        return kind.name().toLowerCase(Locale.ROOT) + "_" + suffix;
    }

    public static boolean acceptTap(Context context, Kind kind) {
        SharedPreferences preferences = prefs(context);
        long now = System.currentTimeMillis();
        long last = preferences.getLong(key(kind, "last_tap"), 0L);
        // A combined Hydra command also reserves MUSIC while the phone is
        // waking.  A later explicit media tap must be allowed to supersede
        // that reservation (after the normal debounce); markPending() below
        // will replace the request id and correlation will reject the late
        // Hydra music acknowledgement. LIVE keeps its strict single-flight.
        long pendingAt = kind == Kind.MUSIC
                ? 0L
                : preferences.getLong(key(kind, "pending_at"), 0L);
        if (!LiveTransitionPolicy.acceptsTap(
                last, pendingAt, now, pendingTimeout(preferences, kind), TAP_DEBOUNCE_MS)) return false;
        preferences.edit().putLong(key(kind, "last_tap"), now).apply();
        return true;
    }

    /** Atomically reserves one combined live + music launcher action. */
    public static boolean acceptMagicTap(Context context) {
        SharedPreferences preferences = prefs(context);
        long now = System.currentTimeMillis();
        if (MagicSettlePolicy.blocksTap(
                preferences.getLong(MAGIC_SETTLE_UNTIL, 0L), now)) {
            // Strict no-op: do not update last_tap or extend the settle window.
            return false;
        }
        for (Kind kind : Kind.values()) {
            long last = preferences.getLong(key(kind, "last_tap"), 0L);
            long pendingAt = preferences.getLong(key(kind, "pending_at"), 0L);
            if (!LiveTransitionPolicy.acceptsTap(
                    last, pendingAt, now, pendingTimeout(preferences, kind), TAP_DEBOUNCE_MS)) {
                return false;
            }
        }
        preferences.edit()
                .putLong(key(Kind.LIVE, "last_tap"), now)
                .putLong(key(Kind.MUSIC, "last_tap"), now)
                .apply();
        return true;
    }

    /** Persists one exact final-start confirmation before its strong haptic. */
    public static boolean beginMagicSettle(
            Context context,
            String requestId,
            long confirmedAtMs) {
        synchronized (MAGIC_SETTLE_LOCK) {
            SharedPreferences preferences = prefs(context);
            if (!MagicSettlePolicy.isNewConfirmation(
                    preferences.getString(MAGIC_SETTLE_REQUEST_ID, ""),
                    requestId)) {
                return false;
            }
            long lockUntilMs = MagicSettlePolicy.lockUntil(confirmedAtMs);
            if (lockUntilMs <= 0L) return false;
            return preferences.edit()
                    .putString(MAGIC_SETTLE_REQUEST_ID, requestId.trim())
                    .putLong(MAGIC_SETTLE_UNTIL, lockUntilMs)
                    .commit();
        }
    }

    public static void markPending(Context context, Kind kind, String requestId) {
        markPending(context, kind, requestId, PENDING_TIMEOUT_MS);
    }

    public static void markPending(
            Context context,
            Kind kind,
            String requestId,
            long timeoutMs) {
        SharedPreferences preferences = prefs(context);
        SharedPreferences.Editor editor = preferences.edit()
                .putString(key(kind, "pending_id"), requestId)
                .putLong(key(kind, "pending_at"), System.currentTimeMillis())
                .putLong(key(kind, "pending_timeout"), Math.max(1_000L, timeoutMs))
                .putString(key(kind, "authoritative_request_id"), requestId)
                .putString(key(kind, "transport"), "sending");
        if (kind == Kind.LIVE) {
            State current = readState(preferences, key(Kind.LIVE, "state"));
            State confirmed = readState(
                    preferences, key(Kind.LIVE, "confirmed_state"), current);
            LivePhase phase = LiveTransitionPolicy.directionFor(confirmed);
            editor.putString(key(Kind.LIVE, "phase"), phase.name())
                    .putLong(key(Kind.LIVE, "phase_started_at"), System.currentTimeMillis())
                    .putString(key(Kind.LIVE, "phase_request_id"), requestId)
                    .putBoolean(key(Kind.LIVE, "phase_confirmed"), false);
        }
        editor.apply();
    }

    /**
     * Commits both combined pending ids and their process-recovery coordinator
     * in one device-protected SharedPreferences transaction.
     */
    public static boolean beginMagicPendingDurable(
            Context context,
            String requestId,
            long timeoutMs,
            String encodedCoordinatorState) {
        String request = requestId == null ? "" : requestId.trim();
        String encoded = encodedCoordinatorState == null
                ? ""
                : encodedCoordinatorState.trim();
        if (request.isEmpty() || request.length() > 256
                || encoded.isEmpty() || encoded.length() > 4_096) return false;
        SharedPreferences preferences = prefs(context);
        long now = System.currentTimeMillis();
        long boundedTimeout = Math.max(1_000L, timeoutMs);
        SharedPreferences.Editor editor = preferences.edit()
                .putString(MAGIC_WAKE_COORDINATOR_STATE, encoded);
        for (Kind kind : Kind.values()) {
            editor.putString(key(kind, "pending_id"), request)
                    .putLong(key(kind, "pending_at"), now)
                    .putLong(key(kind, "pending_timeout"), boundedTimeout)
                    .putString(key(kind, "authoritative_request_id"), request)
                    .putString(key(kind, "transport"), "sending");
        }
        State current = readState(preferences, key(Kind.LIVE, "state"));
        State confirmed = readState(
                preferences, key(Kind.LIVE, "confirmed_state"), current);
        LivePhase phase = LiveTransitionPolicy.directionFor(confirmed);
        editor.putString(key(Kind.LIVE, "phase"), phase.name())
                .putLong(key(Kind.LIVE, "phase_started_at"), now)
                .putString(key(Kind.LIVE, "phase_request_id"), request)
                .putBoolean(key(Kind.LIVE, "phase_confirmed"), false);
        return editor.commit();
    }

    public static String readMagicWakeCoordinatorState(Context context) {
        return prefs(context).getString(MAGIC_WAKE_COORDINATOR_STATE, "");
    }

    public static boolean writeMagicWakeCoordinatorState(
            Context context, String encodedCoordinatorState) {
        String encoded = encodedCoordinatorState == null
                ? ""
                : encodedCoordinatorState.trim();
        return !encoded.isEmpty()
                && encoded.length() <= 4_096
                && prefs(context).edit()
                .putString(MAGIC_WAKE_COORDINATOR_STATE, encoded)
                .commit();
    }

    public static boolean clearMagicWakeCoordinatorState(Context context) {
        return prefs(context).edit().remove(MAGIC_WAKE_COORDINATOR_STATE).commit();
    }

    public static boolean markTransport(
            Context context,
            Kind kind,
            String requestId,
            boolean sent) {
        SharedPreferences preferences = prefs(context);
        String pendingId = preferences.getString(key(kind, "pending_id"), "");
        if (!ResponseCorrelation.isMatchingCommandError(pendingId, requestId)) return false;
        SharedPreferences.Editor editor = preferences.edit()
                .putString(key(kind, "transport"), sent ? "sent" : "offline");
        if (sent) {
            editor.putString(key(kind, "authoritative_request_id"), requestId);
        }
        if (!sent) {
            editor.remove(key(kind, "pending_id"))
                    .remove(key(kind, "pending_at"))
                    .remove(key(kind, "pending_timeout"));
            if (kind == Kind.LIVE) clearLivePhase(editor);
        }
        editor.apply();
        return true;
    }

    public static boolean shouldApply(Context context, Kind kind, String responseId) {
        SharedPreferences preferences = prefs(context);
        String pendingId = preferences.getString(key(kind, "pending_id"), "");
        String authoritativeRequestId = preferences.getString(
                key(kind, "authoritative_request_id"), "");
        return ResponseCorrelation.shouldApplyState(
                pendingId, authoritativeRequestId, responseId);
    }

    public static boolean updateLive(
            Context context,
            String responseId,
            String state,
            String status,
            boolean armed,
            String selectedMic,
            String actualMic) {
        if (!shouldApply(context, Kind.LIVE, responseId)) return false;
        SharedPreferences preferences = prefs(context);
        State normalizedState = normalizeState(state);
        SharedPreferences.Editor editor = preferences.edit()
                .putString(key(Kind.LIVE, "state"), normalizedState.name())
                .putBoolean(key(Kind.LIVE, "armed"), armed)
                .putString(key(Kind.LIVE, "status"), safe(status))
                .putString(key(Kind.LIVE, "selected_mic"), safe(selectedMic))
                .putString(key(Kind.LIVE, "actual_mic"), safe(actualMic))
                .putString(key(Kind.LIVE, "transport"), "received")
                .putLong(key(Kind.LIVE, "updated_at"), System.currentTimeMillis())
                .remove(key(Kind.LIVE, "error"));
        if (normalizedState != State.ERROR) {
            editor.putString(key(Kind.LIVE, "confirmed_state"), normalizedState.name());
        } else {
            clearLivePhase(editor);
        }
        if (normalizedState != State.ERROR) {
            String phaseRequestId = preferences.getString(
                    key(Kind.LIVE, "phase_request_id"), "");
            if (ResponseCorrelation.isMatchingCommandError(phaseRequestId, responseId)) {
                editor.putBoolean(key(Kind.LIVE, "phase_confirmed"), true);
            }
        }
        clearMatchingPending(preferences, editor, Kind.LIVE, responseId);
        editor.apply();
        return true;
    }

    public static boolean updateMusic(
            Context context,
            String responseId,
            String state,
            String packageName,
            String title) {
        if (!shouldApply(context, Kind.MUSIC, responseId)) return false;
        SharedPreferences preferences = prefs(context);
        SharedPreferences.Editor editor = preferences.edit()
                .putString(key(Kind.MUSIC, "state"), normalizeState(state).name())
                .putString(key(Kind.MUSIC, "package"), safe(packageName))
                .putString(key(Kind.MUSIC, "title"), safe(title))
                .putString(key(Kind.MUSIC, "transport"), "received")
                .putLong(key(Kind.MUSIC, "updated_at"), System.currentTimeMillis())
                .remove(key(Kind.MUSIC, "error"));
        clearMatchingPending(preferences, editor, Kind.MUSIC, responseId);
        editor.apply();
        return true;
    }

    public static Kind matchingPendingKind(Context context, String responseId) {
        if (responseId == null || responseId.isEmpty()) return null;
        SharedPreferences preferences = prefs(context);
        for (Kind kind : Kind.values()) {
            String pendingId = preferences.getString(key(kind, "pending_id"), "");
            if (ResponseCorrelation.isMatchingCommandError(pendingId, responseId)) return kind;
        }
        return null;
    }

    public static boolean isMatchingPending(
            Context context,
            Kind kind,
            String responseId) {
        if (kind == null || responseId == null || responseId.isEmpty()) return false;
        String pendingId = prefs(context).getString(key(kind, "pending_id"), "");
        return ResponseCorrelation.isMatchingCommandError(pendingId, responseId);
    }

    public static boolean updateCommandError(
            Context context,
            Kind kind,
            String responseId,
            String error) {
        if (kind == null || responseId == null || responseId.isEmpty()
                || error == null || error.trim().isEmpty()) return false;
        SharedPreferences preferences = prefs(context);
        String pendingId = preferences.getString(key(kind, "pending_id"), "");
        if (!ResponseCorrelation.isMatchingCommandError(pendingId, responseId)) return false;
        SharedPreferences.Editor editor = preferences.edit()
                .putString(key(kind, "state"), State.ERROR.name())
                .putString(key(kind, "error"), error.trim())
                .putString(key(kind, "transport"), "received")
                .putLong(key(kind, "updated_at"), System.currentTimeMillis())
                .remove(key(kind, "pending_id"))
                .remove(key(kind, "pending_at"))
                .remove(key(kind, "pending_timeout"));
        if (kind == Kind.LIVE) clearLivePhase(editor);
        editor.apply();
        return true;
    }

    /** Stores command diagnostics without destroying the exact final state snapshot. */
    public static void recordCommandError(Context context, Kind kind, String error) {
        if (kind == null || error == null || error.trim().isEmpty()) return;
        prefs(context).edit()
                .putString(key(kind, "error"), error.trim())
                .putLong(key(kind, "updated_at"), System.currentTimeMillis())
                .apply();
    }

    /**
     * Stores the local Watch microphone state independently from the phone's
     * translator state. Returns true only when the active/inactive bit changed.
     */
    public static boolean updateWatchMicrophone(
            Context context, boolean active, String status) {
        SharedPreferences preferences = prefs(context);
        boolean previous = preferences.getBoolean(key(Kind.LIVE, "watch_mic_active"), false);
        preferences.edit()
                .putBoolean(key(Kind.LIVE, "watch_mic_active"), active)
                .putString(key(Kind.LIVE, "watch_mic_status"), safe(status))
                .putLong(key(Kind.LIVE, "watch_mic_updated_at"), System.currentTimeMillis())
                .apply();
        return previous != active;
    }

    private static void clearMatchingPending(
            SharedPreferences preferences,
            SharedPreferences.Editor editor,
            Kind kind,
            String responseId) {
        String pendingId = preferences.getString(key(kind, "pending_id"), "");
        long pendingAt = preferences.getLong(key(kind, "pending_at"), 0L);
        if (ResponseCorrelation.shouldClearPending(
                pendingId,
                pendingAt,
                responseId,
                System.currentTimeMillis(),
                pendingTimeout(preferences, kind))) {
            if (responseId != null && !responseId.isEmpty()) {
                editor.putString(key(kind, "authoritative_request_id"), responseId);
            }
            editor.remove(key(kind, "pending_id"))
                    .remove(key(kind, "pending_at"))
                    .remove(key(kind, "pending_timeout"));
        }
    }

    public static Snapshot snapshot(Context context, Kind kind) {
        SharedPreferences preferences = prefs(context);
        State state = readState(preferences, key(kind, "state"));
        long pendingAt = preferences.getLong(key(kind, "pending_at"), 0L);
        long timeoutMs = pendingTimeout(preferences, kind);
        boolean pending = pendingAt > 0L
                && System.currentTimeMillis() - pendingAt <= timeoutMs;
        boolean timedOut = pendingAt > 0L
                && System.currentTimeMillis() - pendingAt > timeoutMs;
        State confirmedState = kind == Kind.LIVE
                ? readState(preferences, key(Kind.LIVE, "confirmed_state"), state)
                : state;
        LivePhase livePhase = kind == Kind.LIVE
                ? readLivePhase(preferences)
                : LivePhase.NONE;
        long phaseStartedAt = kind == Kind.LIVE
                ? preferences.getLong(key(Kind.LIVE, "phase_started_at"), 0L)
                : 0L;
        boolean phaseConfirmed = kind == Kind.LIVE
                && preferences.getBoolean(key(Kind.LIVE, "phase_confirmed"), false);
        long now = System.currentTimeMillis();
        if (kind == Kind.LIVE && (timedOut || LiveTransitionPolicy.canClear(
                livePhase, phaseConfirmed, phaseStartedAt, now))) {
            preferences.edit()
                    .remove(key(Kind.LIVE, "phase"))
                    .remove(key(Kind.LIVE, "phase_started_at"))
                    .remove(key(Kind.LIVE, "phase_request_id"))
                    .remove(key(Kind.LIVE, "phase_confirmed"))
                    .apply();
            livePhase = LivePhase.NONE;
            phaseStartedAt = 0L;
            phaseConfirmed = false;
        }
        return new Snapshot(
                state,
                confirmedState,
                livePhase,
                phaseStartedAt,
                phaseConfirmed,
                pending,
                timedOut,
                preferences.getBoolean(key(kind, "armed"), false),
                preferences.getString(key(kind, "transport"), "unknown"),
                preferences.getString(key(kind, "status"), ""),
                preferences.getString(key(kind, "selected_mic"), ""),
                preferences.getString(key(kind, "actual_mic"), ""),
                kind == Kind.LIVE
                        && preferences.getBoolean(key(Kind.LIVE, "watch_mic_active"), false),
                kind == Kind.LIVE
                        ? preferences.getString(key(Kind.LIVE, "watch_mic_status"), "")
                        : "",
                preferences.getString(key(kind, "package"), ""),
                preferences.getString(key(kind, "title"), ""),
                preferences.getString(key(kind, "error"), ""));
    }

    /** Returns -1 when there is no confirmed live transition waiting to finish. */
    public static long livePhaseClearDelay(Context context) {
        SharedPreferences preferences = prefs(context);
        return LiveTransitionPolicy.clearDelayMs(
                readLivePhase(preferences),
                preferences.getBoolean(key(Kind.LIVE, "phase_confirmed"), false),
                preferences.getLong(key(Kind.LIVE, "phase_started_at"), 0L),
                System.currentTimeMillis());
    }

    /** Clears only the same confirmed transition after its minimum visible time. */
    public static boolean clearLivePhaseIfDue(Context context) {
        SharedPreferences preferences = prefs(context);
        LivePhase phase = readLivePhase(preferences);
        boolean confirmed = preferences.getBoolean(
                key(Kind.LIVE, "phase_confirmed"), false);
        long startedAt = preferences.getLong(
                key(Kind.LIVE, "phase_started_at"), 0L);
        if (!LiveTransitionPolicy.canClear(
                phase, confirmed, startedAt, System.currentTimeMillis())) return false;
        SharedPreferences.Editor editor = preferences.edit();
        clearLivePhase(editor);
        editor.apply();
        return true;
    }

    public static boolean shouldRequestState(Context context) {
        SharedPreferences preferences = prefs(context);
        long now = System.currentTimeMillis();
        long last = preferences.getLong("last_state_request", 0L);
        if (now - last < 5_000L) return false;
        preferences.edit().putLong("last_state_request", now).apply();
        return true;
    }

    private static State normalizeState(String raw) {
        if (raw == null) return State.UNKNOWN;
        switch (raw.trim().toLowerCase(Locale.ROOT)) {
            case "running":
            case "active":
            case "playing":
            case "listening":
            case "on":
                return State.RUNNING;
            case "paused":
            case "pause":
                return State.PAUSED;
            case "stopped":
            case "stop":
            case "idle":
            case "ready":
            case "none":
            case "off":
                return State.STOPPED;
            case "error":
            case "failed":
                return State.ERROR;
            default:
                return State.UNKNOWN;
        }
    }

    private static long pendingTimeout(SharedPreferences preferences, Kind kind) {
        return Math.max(
                1_000L,
                preferences.getLong(key(kind, "pending_timeout"), PENDING_TIMEOUT_MS));
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }

    private static State readState(SharedPreferences preferences, String stateKey) {
        return readState(preferences, stateKey, State.UNKNOWN);
    }

    private static State readState(
            SharedPreferences preferences,
            String stateKey,
            State fallback) {
        try {
            return State.valueOf(preferences.getString(stateKey, fallback.name()));
        } catch (IllegalArgumentException ignored) {
            return fallback;
        }
    }

    private static LivePhase readLivePhase(SharedPreferences preferences) {
        try {
            return LivePhase.valueOf(preferences.getString(
                    key(Kind.LIVE, "phase"), LivePhase.NONE.name()));
        } catch (IllegalArgumentException ignored) {
            return LivePhase.NONE;
        }
    }

    private static void clearLivePhase(SharedPreferences.Editor editor) {
        editor.remove(key(Kind.LIVE, "phase"))
                .remove(key(Kind.LIVE, "phase_started_at"))
                .remove(key(Kind.LIVE, "phase_request_id"))
                .remove(key(Kind.LIVE, "phase_confirmed"));
    }

    public static final class Snapshot {
        public final State state;
        public final State confirmedState;
        public final LivePhase livePhase;
        public final long livePhaseStartedAt;
        public final boolean livePhaseConfirmed;
        public final boolean pending;
        public final boolean timedOut;
        public final boolean armed;
        public final String transport;
        public final String status;
        public final String selectedMic;
        public final String actualMic;
        public final boolean watchMicActive;
        public final String watchMicStatus;
        public final String packageName;
        public final String title;
        public final String error;

        private Snapshot(
                State state,
                State confirmedState,
                LivePhase livePhase,
                long livePhaseStartedAt,
                boolean livePhaseConfirmed,
                boolean pending,
                boolean timedOut,
                boolean armed,
                String transport,
                String status,
                String selectedMic,
                String actualMic,
                boolean watchMicActive,
                String watchMicStatus,
                String packageName,
                String title,
                String error) {
            this.state = state;
            this.confirmedState = confirmedState;
            this.livePhase = livePhase;
            this.livePhaseStartedAt = livePhaseStartedAt;
            this.livePhaseConfirmed = livePhaseConfirmed;
            this.pending = pending;
            this.timedOut = timedOut;
            this.armed = armed;
            this.transport = transport;
            this.status = status;
            this.selectedMic = selectedMic;
            this.actualMic = actualMic;
            this.watchMicActive = watchMicActive;
            this.watchMicStatus = watchMicStatus;
            this.packageName = packageName;
            this.title = title;
            this.error = error;
        }
    }
}
