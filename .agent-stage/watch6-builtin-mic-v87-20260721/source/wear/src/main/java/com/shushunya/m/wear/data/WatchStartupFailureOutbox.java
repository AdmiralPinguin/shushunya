package com.shushunya.m.wear.data;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.shushunya.m.wear.audio.WearAudioLifecycleProtocol;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Durable exact startup-failure outbox; only an application ACK completes delivery. */
public final class WatchStartupFailureOutbox {
    private static final String TAG = "ShushunyaFailureOutbox";
    private static final String PREFS = "watch_startup_failure_outbox_v1";
    private static final String KEY_NODE = "node";
    private static final String KEY_REQUEST = "request";
    private static final String KEY_CODE = "code";
    private static final String KEY_FAILED_AT = "failed_at";
    private static final String KEY_PAYLOAD = "payload";
    private static final String KEY_NEXT_ATTEMPT = "next_attempt";
    private static final AtomicBoolean PUMPING = new AtomicBoolean(false);
    private static final AtomicLong DELIVERY_WINDOW = new AtomicLong(0L);

    private WatchStartupFailureOutbox() {}

    public static boolean publish(
            Context context,
            String phoneNodeId,
            String requestId,
            String code,
            String detail) {
        if (context == null) return false;
        String node = clean(phoneNodeId);
        long failedAtMs = System.currentTimeMillis();
        String payload = WearAudioLifecycleProtocol.startupFailureJson(
                requestId, code, detail, failedAtMs);
        if (node.isEmpty() || payload.isEmpty()) return false;
        SharedPreferences prefs = prefs(context);
        boolean stored;
        synchronized (WatchStartupFailureOutbox.class) {
            stored = prefs.edit()
                    .putString(KEY_NODE, node)
                    .putString(KEY_REQUEST, clean(requestId))
                    .putString(KEY_CODE, clean(code))
                    .putLong(KEY_FAILED_AT, failedAtMs)
                    .putString(KEY_PAYLOAD, payload)
                    .putInt(KEY_NEXT_ATTEMPT, 0)
                    .commit();
        }
        if (stored) {
            ensureDelivery(context);
        }
        return stored;
    }

    public static void ensureDelivery(Context context) {
        if (context == null || !hasPending(context)) return;
        // A new external app/Wear/boot event starts one more finite burst. The
        // delivery service itself calls resume(), so it cannot turn its own
        // exhausted burst into an endless foreground loop.
        synchronized (WatchStartupFailureOutbox.class) {
            SharedPreferences prefs = prefs(context);
            Pending pending = read(prefs);
            if (pending != null
                    && StartupFailureDeliveryPolicy.isExhausted(pending.nextAttempt)) {
                prefs.edit().putInt(KEY_NEXT_ATTEMPT, 0).commit();
            }
        }
        // Invalidate any callbacks from an older delivery window before this
        // external event starts its own bounded seven-attempt burst.
        DELIVERY_WINDOW.incrementAndGet();
        PUMPING.set(false);
        WatchStartupFailureDeliveryService.start(context);
        resume(context);
    }

    static boolean hasPending(Context context) {
        if (context == null) return false;
        synchronized (WatchStartupFailureOutbox.class) {
            return read(prefs(context)) != null;
        }
    }

    public static void resume(Context context) {
        if (context == null || !PUMPING.compareAndSet(false, true)) return;
        pump(context.getApplicationContext(), DELIVERY_WINDOW.get());
    }

    /** Hard-deadline fence: already-posted Handler callbacks become inert. */
    static void cancelDeliveryWindow() {
        DELIVERY_WINDOW.incrementAndGet();
        PUMPING.set(false);
    }

    public static boolean acknowledge(
            Context context,
            String sourceNodeId,
            String requestId,
            String code,
            long failedAtMs) {
        if (context == null) return false;
        synchronized (WatchStartupFailureOutbox.class) {
            SharedPreferences prefs = prefs(context);
            Pending pending = read(prefs);
            if (pending == null || !StartupFailureDeliveryPolicy.sameDelivery(
                    pending.node,
                    pending.requestId,
                    pending.code,
                    pending.failedAtMs,
                    sourceNodeId,
                    requestId,
                    code,
                    failedAtMs)) return false;
            boolean cleared = prefs.edit().clear().commit();
            if (cleared) {
                Log.i(TAG, "Application ACK completed request=" + requestId);
                WatchStartupFailureDeliveryService.stop(context);
            }
            return cleared;
        }
    }

    private static void pump(Context app, long window) {
        if (DELIVERY_WINDOW.get() != window) return;
        Pending pending;
        int attempt;
        long delayMs;
        synchronized (WatchStartupFailureOutbox.class) {
            SharedPreferences prefs = prefs(app);
            pending = read(prefs);
            if (pending == null) {
                stopPumpOrResume(app, window);
                return;
            }
            attempt = pending.nextAttempt;
            delayMs = StartupFailureDeliveryPolicy.delayForAttempt(attempt);
            if (delayMs < 0L) {
                PUMPING.set(false);
                return;
            }
        }
        Pending exact = pending;
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            if (DELIVERY_WINDOW.get() != window) return;
            Pending current;
            synchronized (WatchStartupFailureOutbox.class) {
                current = read(prefs(app));
            }
            if (current == null || !current.sameIdentity(exact)) {
                stopPumpOrResume(app, window);
                return;
            }
            // MessageClient Task success is only local queue acceptance. Never
            // stop here; only acknowledge() may clear the durable outbox.
            WearMessageSender.sendToTarget(
                    app,
                    exact.node,
                    WearAudioLifecycleProtocol.PATH_STARTUP_FAILURE,
                    exact.payload);
            // Consume an attempt only after invoking MessageClient. A process
            // death before this point therefore replays instead of silently
            // spending a persisted attempt. One external trigger gets exactly
            // one bounded burst; an ACK is still the only completion signal.
            synchronized (WatchStartupFailureOutbox.class) {
                Pending afterSend = read(prefs(app));
                if (afterSend != null && afterSend.sameIdentity(exact)) {
                    prefs(app).edit().putInt(
                            KEY_NEXT_ATTEMPT,
                            StartupFailureDeliveryPolicy.nextAttempt(attempt)).commit();
                }
            }
            if (StartupFailureDeliveryPolicy.shouldRetry(attempt + 1, false)) {
                pump(app, window);
            } else {
                PUMPING.set(false);
            }
        }, delayMs);
    }

    private static void stopPumpOrResume(Context app, long window) {
        if (DELIVERY_WINDOW.get() != window) return;
        PUMPING.set(false);
        synchronized (WatchStartupFailureOutbox.class) {
            if (DELIVERY_WINDOW.get() == window
                    && read(prefs(app)) != null
                    && PUMPING.compareAndSet(false, true)) {
                pump(app, window);
            }
        }
    }

    private static Pending read(SharedPreferences prefs) {
        String node = clean(prefs.getString(KEY_NODE, ""));
        String request = clean(prefs.getString(KEY_REQUEST, ""));
        String code = clean(prefs.getString(KEY_CODE, ""));
        long failedAtMs = prefs.getLong(KEY_FAILED_AT, 0L);
        String payload = prefs.getString(KEY_PAYLOAD, "");
        if (node.isEmpty() || request.isEmpty() || code.isEmpty()
                || failedAtMs <= 0L || payload == null || payload.isEmpty()) return null;
        return new Pending(
                node, request, code, failedAtMs, payload,
                Math.max(0, prefs.getInt(KEY_NEXT_ATTEMPT, 0)));
    }

    private static SharedPreferences prefs(Context context) {
        Context app = context.getApplicationContext();
        return app.createDeviceProtectedStorageContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static final class Pending {
        final String node;
        final String requestId;
        final String code;
        final long failedAtMs;
        final String payload;
        final int nextAttempt;

        Pending(
                String node,
                String requestId,
                String code,
                long failedAtMs,
                String payload,
                int nextAttempt) {
            this.node = node;
            this.requestId = requestId;
            this.code = code;
            this.failedAtMs = failedAtMs;
            this.payload = payload;
            this.nextAttempt = nextAttempt;
        }

        boolean sameIdentity(Pending other) {
            return other != null && StartupFailureDeliveryPolicy.sameDelivery(
                    node, requestId, code, failedAtMs,
                    other.node, other.requestId, other.code, other.failedAtMs);
        }
    }
}
