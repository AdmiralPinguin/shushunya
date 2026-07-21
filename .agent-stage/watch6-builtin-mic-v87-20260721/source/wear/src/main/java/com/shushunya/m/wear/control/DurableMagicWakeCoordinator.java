package com.shushunya.m.wear.control;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.widget.Toast;

import androidx.wear.remote.interactions.RemoteActivityHelper;

import com.shushunya.m.wear.audio.WearMicForegroundService;
import com.shushunya.m.wear.data.ComplicationRefresh;
import com.shushunya.m.wear.data.ControllerStateStore;
import com.shushunya.m.wear.data.MagicAcceptedRegistry;
import com.shushunya.m.wear.data.PreparedAckRegistry;
import com.shushunya.m.wear.data.WearMessageSender;
import com.shushunya.m.wear.data.WearProtocol;

import java.util.Collections;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Process-recoverable owner of one exact RemoteActivity command.
 *
 * The controller-only command keepalive calls {@link #resumeIfNeeded(Context)}
 * after Android redelivers it. All authority lives in the durable
 * UUID/node/phase record; callbacks and retry indices are merely reconstructed
 * from that record.
 */
public final class DurableMagicWakeCoordinator {
    public static final String ACTION_EVENT =
            "com.shushunya.m.wear.control.MAGIC_WAKE_COORDINATOR_EVENT";
    public static final String EXTRA_REQUEST_ID = "request_id";
    public static final String EXTRA_EVENT = "event";
    public static final String EXTRA_PHONE_NODE_ID = "phone_node_id";
    public static final String EXTRA_TARGET_START = "target_start";
    public static final String EXTRA_EXACT_STARTED = "exact_started";
    public static final String EXTRA_HAS_ERROR = "has_error";
    public static final String EVENT_ACCEPTED = "accepted";
    public static final String EVENT_TERMINAL = "terminal";
    public static final String EVENT_FAILED = "failed";

    private static final String TAG = "ShushunyaMagicWake";
    private static final long DISCOVERY_TIMEOUT_MS = 25_000L;
    private static final long FINAL_TIMEOUT_MS = 65_000L;
    private static final long ACCEPT_POLL_MS = 100L;
    private static final int LAST_DISCOVERY_ATTEMPT_INDEX = 8; // 24 seconds
    private static final Uri REMOTE_MAGIC_TOGGLE_URI =
            Uri.parse("shushunya://wear/magic/toggle");
    private static final ControllerStateStore.Kind[] MAGIC_KINDS = {
            ControllerStateStore.Kind.LIVE,
            ControllerStateStore.Kind.MUSIC
    };
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final AtomicLong GENERATION = new AtomicLong();

    private DurableMagicWakeCoordinator() {}

    /** Persists the UUID before any asynchronous node discovery begins. */
    public static boolean begin(Context context, String requestId, long issuedAtMs) {
        Context app = context.getApplicationContext();
        if (!MagicWakeCoordinatorStore.begin(
                app, requestId, issuedAtMs, FINAL_TIMEOUT_MS)) return false;
        try {
            MagicCommandForegroundService.startPending(app, requestId);
        } catch (RuntimeException error) {
            Log.w(TAG, "Could not start the MAGIC command keepalive", error);
            failExact(app, requestId, "Не удалось удержать команду переводчика");
            return false;
        }
        resumeIfNeeded(app);
        return true;
    }

    /** Called from START_REDELIVER_INTENT recovery and after every durable phase change. */
    public static void resumeIfNeeded(Context context) {
        Context app = context.getApplicationContext();
        long generation = GENERATION.incrementAndGet();
        MAIN.post(() -> resumeGeneration(app, generation));
    }

    /** Commits ACCEPTED before the one-shot light haptic. */
    public static boolean markAcceptedExact(
            Context context,
            String requestId,
            String sourceNodeId,
            boolean targetStart) {
        Context app = context.getApplicationContext();
        MagicWakeCoordinatorStore.AcceptedResult result =
                MagicWakeCoordinatorStore.markAcceptedExact(
                        app, requestId, sourceNodeId, targetStart);
        if (result == MagicWakeCoordinatorStore.AcceptedResult.REJECTED) return false;
        MagicAcceptedRegistry.discard(requestId);
        PreparedAckRegistry.discard(requestId);
        if (result == MagicWakeCoordinatorStore.AcceptedResult.CHANGED) {
            markRemoteTransport(app, requestId, true);
            Haptics.tick(app);
            broadcastEvent(
                    app,
                    requestId,
                    EVENT_ACCEPTED,
                    sourceNodeId,
                    targetStart,
                    false,
                    false);
            resumeIfNeeded(app);
        }
        return true;
    }

    /** Replays an unconsumed durable ACCEPTED action only to a visible Activity receiver. */
    public static void replayAcceptedActionIfNeeded(Context context) {
        Context app = context.getApplicationContext();
        MagicWakeCoordinatorState state = MagicWakeCoordinatorStore.read(app);
        if (state == null || !AcceptedActionReplayPolicy.shouldReplay(
                state.isAccepted(),
                state.acceptedActionConsumed,
                isExactPending(app, state.requestId))) return;
        broadcastEvent(
                app,
                state.requestId,
                EVENT_ACCEPTED,
                state.phoneNodeId,
                state.targetStart,
                false,
                false);
    }

    /** Commits that the visible Activity safely applied START/STOP for this exact ACK. */
    public static boolean markAcceptedActionConsumedExact(
            Context context,
            String requestId,
            String phoneNodeId,
            boolean targetStart) {
        return MagicWakeCoordinatorStore.markAcceptedActionConsumedExact(
                context.getApplicationContext(), requestId, phoneNodeId, targetStart);
    }

    /** Returns the immutable accepted direction for this exact pending MAGIC UUID. */
    public static Boolean pendingTargetStartExact(Context context, String requestId) {
        String exact = requestId == null ? "" : requestId.trim();
        MagicWakeCoordinatorState state = MagicWakeCoordinatorStore.read(
                context.getApplicationContext());
        if (state == null
                || !state.requestId.equals(exact)
                || !state.isAccepted()
                || !isExactPending(context, exact)) return null;
        return state.targetStart;
    }

    /** Exact correlated terminal state is the only successful cleanup path. */
    public static void completeExact(Context context, String requestId) {
        completeExact(context, requestId, false, false, false);
    }

    /** Exact phone terminal, including a deduped haptic for cross-path reordering. */
    public static void completeExactTerminal(
            Context context,
            String requestId,
            boolean hasError,
            boolean exactStarted) {
        completeExact(context, requestId, true, hasError, exactStarted);
    }

    private static void completeExact(
            Context context,
            String requestId,
            boolean terminalConfirmation,
            boolean hasError,
            boolean exactStarted) {
        Context app = context.getApplicationContext();
        if (requestId == null || requestId.trim().isEmpty()) return;
        MagicWakeCoordinatorState before = MagicWakeCoordinatorStore.read(app);
        boolean exact = before != null && requestId.trim().equals(before.requestId);
        boolean exactTargetStart = exact && before.isAccepted() && before.targetStart;
        TerminalFirstConfirmationPolicy.Haptic fallback =
                !terminalConfirmation || !exact
                        ? TerminalFirstConfirmationPolicy.Haptic.NONE
                        : TerminalFirstConfirmationPolicy.decide(
                                before.isAccepted(), hasError, exactStarted);
        boolean cleared = MagicWakeCoordinatorStore.clearExact(app, requestId);
        GENERATION.incrementAndGet();
        PreparedAckRegistry.discard(requestId);
        MagicAcceptedRegistry.discard(requestId);
        if (cleared) MagicCommandForegroundService.stop(app);
        if (cleared && fallback == TerminalFirstConfirmationPolicy.Haptic.LIGHT_SUCCESS) {
            Haptics.tick(app);
        } else if (cleared && fallback == TerminalFirstConfirmationPolicy.Haptic.FAILURE) {
            Haptics.failure(app);
        }
        broadcastEvent(
                app,
                requestId,
                EVENT_TERMINAL,
                before == null ? "" : before.phoneNodeId,
                exactTargetStart,
                exactStarted,
                hasError);
    }

    /** Rolls back the atomic pending/store authority when command keepalive creation fails. */
    public static void abortStartExact(
            Context context, String requestId, String message) {
        failExact(
                context.getApplicationContext(),
                requestId,
                message == null ? "Не удалось удержать команду переводчика" : message);
    }

    private static void resumeGeneration(Context app, long generation) {
        if (generation != GENERATION.get()) return;
        MagicWakeCoordinatorState state = MagicWakeCoordinatorStore.read(app);
        if (state == null) return;
        if (!isExactPending(app, state.requestId)) {
            completeExact(app, state.requestId);
            return;
        }
        if (state.phase == MagicWakeCoordinatorState.Phase.DISCOVERING) {
            resumeDiscovery(app, state, generation);
            return;
        }
        consumeQueuedAcceptance(app, state);
        state = MagicWakeCoordinatorStore.read(app);
        if (state == null || generation != GENERATION.get()) return;
        scheduleDeadlines(app, state, generation);
        scheduleAcceptedPoll(app, state.requestId, generation);
        scheduleNextWake(app, state, generation);
    }

    private static void resumeDiscovery(
            Context app,
            MagicWakeCoordinatorState state,
            long generation) {
        if (elapsedSince(state.issuedAtMs) >= DISCOVERY_TIMEOUT_MS) {
            failExact(app, state.requestId,
                    "Телефон не принял защищённую команду переводчика");
            return;
        }
        int attemptIndex = state.nextAttemptIndex;
        long scheduleAtMs = RemoteWakeRetryPolicy.delayForIndex(attemptIndex);
        if (scheduleAtMs < 0L || attemptIndex > LAST_DISCOVERY_ATTEMPT_INDEX) {
            MAIN.postDelayed(
                    () -> {
                        if (generation != GENERATION.get()) return;
                        MagicWakeCoordinatorState current =
                                MagicWakeCoordinatorStore.read(app);
                        if (current != null
                                && current.phase
                                == MagicWakeCoordinatorState.Phase.DISCOVERING
                                && state.requestId.equals(current.requestId)) {
                            failExact(app, current.requestId,
                                    "Телефон не принял защищённую команду переводчика");
                        }
                    },
                    remainingDelay(state.issuedAtMs, DISCOVERY_TIMEOUT_MS));
            return;
        }
        MAIN.postDelayed(
                () -> runDiscoveryAttempt(
                        app, state.requestId, attemptIndex, generation),
                remainingDelay(state.issuedAtMs, scheduleAtMs));
    }

    private static void runDiscoveryAttempt(
            Context app,
            String requestId,
            int attemptIndex,
            long generation) {
        if (generation != GENERATION.get()) return;
        MagicWakeCoordinatorState state = MagicWakeCoordinatorStore.read(app);
        if (state == null
                || state.phase != MagicWakeCoordinatorState.Phase.DISCOVERING
                || !requestId.equals(state.requestId)
                || state.nextAttemptIndex != attemptIndex) return;
        if (!isExactPending(app, requestId)) {
            completeExact(app, requestId);
            return;
        }
        if (elapsedSince(state.issuedAtMs) >= DISCOVERY_TIMEOUT_MS) {
            failExact(app, requestId,
                    "Телефон не принял защищённую команду переводчика");
            return;
        }
        String payload = WearProtocol.requestJson(state.requestId, state.issuedAtMs);
        WearMessageSender.sendToNearbyTargets(
                        app, WearProtocol.PATH_MAGIC_PREPARE, payload)
                .addOnCompleteListener(app.getMainExecutor(), task -> {
                    if (generation != GENERATION.get()) return;
                    MagicWakeCoordinatorState current = MagicWakeCoordinatorStore.read(app);
                    if (current == null
                            || current.phase != MagicWakeCoordinatorState.Phase.DISCOVERING
                            || !state.requestId.equals(current.requestId)) return;
                    WearMessageSender.NearbySendResult result =
                            task.isSuccessful() ? task.getResult() : null;
                    if (result == null || !result.anyQueued()) {
                        if (elapsedSince(current.issuedAtMs) >= DISCOVERY_TIMEOUT_MS) {
                            failExact(app, current.requestId,
                                    "Телефон не принял защищённую команду переводчика");
                            return;
                        }
                        int nextIndex = RemoteWakeRetryPolicy.nextIndexAfterAttempt(
                                attemptIndex, elapsedSince(current.issuedAtMs));
                        if (!MagicWakeCoordinatorStore.advanceExact(
                                app, current.requestId, nextIndex)) return;
                        MagicWakeCoordinatorState advanced =
                                MagicWakeCoordinatorStore.read(app);
                        if (advanced != null && generation == GENERATION.get()) {
                            resumeDiscovery(app, advanced, generation);
                        }
                        return;
                    }
                    String preparedNode = PreparedAckRegistry.consumeMatching(
                            current.requestId,
                            result.successfulNodeIds(),
                            SystemClock.elapsedRealtime());
                    RemoteWakeTargetPolicy.Selection selection =
                            RemoteWakeTargetPolicy.select(
                                    preparedNode, result.successfulNodeIds());
                    if (!selection.hasTarget()) {
                        failExact(app, current.requestId,
                                "Телефон не дал однозначный узел для запуска переводчика");
                        return;
                    }
                    long startedAtMs = System.currentTimeMillis();
                    if (!MagicWakeCoordinatorStore.selectNodeExact(
                            app,
                            current.requestId,
                            selection.nodeId,
                            startedAtMs)) {
                        failExact(app, current.requestId,
                                "Не удалось сохранить команду запуска переводчика");
                        return;
                    }
                    MagicAcceptedRegistry.discard(current.requestId);
                    Log.i(TAG, "Durably selected phone request=" + current.requestId
                            + " node=" + selection.nodeId
                            + " preparedAck=" + selection.preparedAckObserved);
                    resumeIfNeeded(app);
                });
    }

    private static void scheduleDeadlines(
            Context app,
            MagicWakeCoordinatorState state,
            long generation) {
        if (!state.isAccepted()) {
            long acceptDelay = remainingUntil(RemoteWakeRetryPolicy.preAcceptDeadlineMs(
                    state.issuedAtMs, state.wakeStartedAtMs));
            MAIN.postDelayed(() -> {
                if (generation != GENERATION.get()) return;
                MagicWakeCoordinatorState current = MagicWakeCoordinatorStore.read(app);
                if (current == null || !state.requestId.equals(current.requestId)
                        || current.isAccepted()) return;
                failExact(app, current.requestId,
                        "Телефон не подтвердил запуск переводчика");
            }, acceptDelay);
        }
        // One authority window: ControllerStateStore pending and coordinator
        // both expire from the original user tap, never from later discovery.
        long finalDelay = remainingDelay(state.issuedAtMs, FINAL_TIMEOUT_MS);
        MAIN.postDelayed(() -> {
            if (generation != GENERATION.get()) return;
            MagicWakeCoordinatorState current = MagicWakeCoordinatorStore.read(app);
            if (current == null || !state.requestId.equals(current.requestId)) return;
            if (!isExactPending(app, current.requestId)) {
                completeExact(app, current.requestId);
                return;
            }
            failExact(app, current.requestId,
                    "Телефон не прислал итоговое состояние переводчика");
        }, finalDelay);
    }

    private static void scheduleAcceptedPoll(
            Context app, String requestId, long generation) {
        MagicWakeCoordinatorState state = MagicWakeCoordinatorStore.read(app);
        if (state == null || state.isAccepted() || generation != GENERATION.get()) return;
        MAIN.postDelayed(() -> {
            if (generation != GENERATION.get()) return;
            MagicWakeCoordinatorState current = MagicWakeCoordinatorStore.read(app);
            if (current == null || !requestId.equals(current.requestId)
                    || current.isAccepted()) return;
            consumeQueuedAcceptance(app, current);
            if (generation == GENERATION.get()) {
                scheduleAcceptedPoll(app, requestId, generation);
            }
        }, ACCEPT_POLL_MS);
    }

    private static void consumeQueuedAcceptance(
            Context app, MagicWakeCoordinatorState state) {
        if (state == null || state.isAccepted() || state.phoneNodeId.isEmpty()) return;
        Set<String> allowedNode = Collections.singleton(state.phoneNodeId);
        MagicAcceptedRegistry.AcceptedAck accepted =
                MagicAcceptedRegistry.consumeMatchingAck(
                state.requestId, allowedNode, SystemClock.elapsedRealtime());
        if (accepted != null) {
            markAcceptedExact(
                    app,
                    state.requestId,
                    accepted.sourceNodeId,
                    accepted.targetStart);
        }
    }

    private static void scheduleNextWake(
            Context app,
            MagicWakeCoordinatorState state,
            long generation) {
        int attemptIndex = state.nextAttemptIndex;
        long scheduledDelay = RemoteWakeRetryPolicy.delayForIndex(attemptIndex);
        if (scheduledDelay < 0L
                || !RemoteWakeRetryPolicy.mayScheduleAttempt(
                        state.isAccepted(),
                        state.issuedAtMs,
                        state.wakeStartedAtMs,
                        attemptIndex)) return;
        long delay = remainingDelay(state.wakeStartedAtMs, scheduledDelay);
        MAIN.postDelayed(
                () -> runWakeAttempt(app, state.requestId, attemptIndex, generation),
                delay);
    }

    private static void runWakeAttempt(
            Context app,
            String requestId,
            int attemptIndex,
            long generation) {
        if (generation != GENERATION.get()) return;
        MagicWakeCoordinatorState state = MagicWakeCoordinatorStore.read(app);
        if (state == null || !requestId.equals(state.requestId)
                || state.nextAttemptIndex != attemptIndex) return;
        boolean exactPending = isExactPending(app, requestId);
        boolean preAcceptTimedOut = RemoteWakeRetryPolicy.preAcceptTimedOut(
                state.isAccepted(),
                state.issuedAtMs,
                state.wakeStartedAtMs,
                System.currentTimeMillis());
        if (!RemoteWakeRetryPolicy.shouldAttempt(
                state.isAccepted(), preAcceptTimedOut, exactPending, attemptIndex)) {
            if (!exactPending) completeExact(app, requestId);
            else if (preAcceptTimedOut) {
                failExact(app, requestId, "Телефон не подтвердил запуск переводчика");
            }
            return;
        }

        String prepareJson = WearProtocol.requestJson(state.requestId, state.issuedAtMs);
        sendTargetedPrepare(
                app, state.phoneNodeId, prepareJson,
                "wake-" + attemptIndex + "-before");
        try {
            RemoteActivityHelper helper =
                    new RemoteActivityHelper(app, app.getMainExecutor());
            var future = helper.startRemoteActivity(
                    buildRemoteToggleIntent(state.requestId, state.issuedAtMs),
                    state.phoneNodeId);
            future.addListener(() -> {
                try {
                    future.get();
                    Log.i(TAG, "RemoteActivity INTENT_SENT request=" + requestId
                            + " attempt=" + attemptIndex);
                    MagicWakeCoordinatorState after = MagicWakeCoordinatorStore.read(app);
                    boolean stillExactPending = isExactPending(app, requestId);
                    boolean timedOutBeforeAccept = after != null
                            && RemoteWakeRetryPolicy.preAcceptTimedOut(
                                    after.isAccepted(),
                                    after.issuedAtMs,
                                    after.wakeStartedAtMs,
                                    System.currentTimeMillis());
                    if (after != null
                            && requestId.equals(after.requestId)
                            && RemoteWakeRetryPolicy.shouldAttempt(
                                    after.isAccepted(),
                                    timedOutBeforeAccept,
                                    stillExactPending,
                                    attemptIndex)) {
                        sendTargetedPrepare(
                                app,
                                after.phoneNodeId,
                                WearProtocol.requestJson(after.requestId, after.issuedAtMs),
                                "wake-" + attemptIndex + "-after");
                    }
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                    Log.w(TAG, "RemoteActivity wake interrupted; retry remains armed", error);
                } catch (Exception error) {
                    Log.w(TAG, "RemoteActivity wake failed; retry remains armed", error);
                }
            }, app.getMainExecutor());
        } catch (RuntimeException error) {
            Log.w(TAG, "RemoteActivity wake rejected; retry remains armed", error);
        }

        long elapsedMs = elapsedSince(state.wakeStartedAtMs);
        int nextIndex = RemoteWakeRetryPolicy.nextIndexAfterAttempt(
                attemptIndex, elapsedMs);
        if (!MagicWakeCoordinatorStore.advanceExact(app, requestId, nextIndex)) return;
        MagicWakeCoordinatorState advanced = MagicWakeCoordinatorStore.read(app);
        if (advanced != null && generation == GENERATION.get()) {
            scheduleNextWake(app, advanced, generation);
        }
    }

    private static Intent buildRemoteToggleIntent(
            String requestId, long issuedAtMs) {
        Uri uri = REMOTE_MAGIC_TOGGLE_URI.buildUpon()
                .appendQueryParameter("requestId", requestId)
                .appendQueryParameter("issuedAtMs", Long.toString(issuedAtMs))
                .build();
        return new Intent(Intent.ACTION_VIEW)
                .setData(uri)
                .addCategory(Intent.CATEGORY_BROWSABLE);
    }

    private static void sendTargetedPrepare(
            Context app,
            String phoneNodeId,
            String prepareJson,
            String attempt) {
        WearMessageSender.sendToTarget(
                        app,
                        phoneNodeId,
                        WearProtocol.PATH_MAGIC_PREPARE,
                        prepareJson)
                .addOnCompleteListener(app.getMainExecutor(), task -> Log.i(
                        TAG,
                        "Targeted PREPARE replay attempt=" + attempt
                                + " node=" + phoneNodeId
                                + " queued="
                                + (task.isSuccessful()
                                && Boolean.TRUE.equals(task.getResult()))));
    }

    private static void failExact(Context app, String requestId, String message) {
        if (requestId == null || requestId.trim().isEmpty()) return;
        MagicWakeCoordinatorState before = MagicWakeCoordinatorStore.read(app);
        boolean failedAcceptedStart = before != null
                && requestId.trim().equals(before.requestId)
                && before.isAccepted()
                && before.targetStart;
        boolean targetStart = before != null
                && requestId.trim().equals(before.requestId)
                && before.isAccepted()
                && before.targetStart;
        String phoneNodeId = before == null ? "" : before.phoneNodeId;
        boolean wasPending = markRemoteTransport(app, requestId, false);
        boolean cleared = MagicWakeCoordinatorStore.clearExact(app, requestId);
        GENERATION.incrementAndGet();
        PreparedAckRegistry.discard(requestId);
        MagicAcceptedRegistry.discard(requestId);
        if (cleared) MagicCommandForegroundService.stop(app);
        if (failedAcceptedStart) WearMicForegroundService.stop(app);
        if (wasPending) {
            Haptics.failure(app);
            Toast.makeText(app, message, Toast.LENGTH_LONG).show();
        }
        broadcastEvent(
                app, requestId, EVENT_FAILED, phoneNodeId, targetStart, false, true);
    }

    private static boolean markRemoteTransport(
            Context context, String requestId, boolean sent) {
        boolean anyCurrent = false;
        for (ControllerStateStore.Kind kind : MAGIC_KINDS) {
            boolean current = ControllerStateStore.markTransport(
                    context, kind, requestId, sent);
            anyCurrent |= current;
            if (current) ComplicationRefresh.request(context, kind);
        }
        return anyCurrent;
    }

    private static boolean isExactPending(Context context, String requestId) {
        return ControllerStateStore.isMatchingPending(
                context, ControllerStateStore.Kind.LIVE, requestId);
    }

    private static long elapsedSince(long startedAtMs) {
        long elapsed = System.currentTimeMillis() - startedAtMs;
        return Math.max(0L, elapsed);
    }

    private static long remainingDelay(long startedAtMs, long durationMs) {
        return Math.max(0L, durationMs - elapsedSince(startedAtMs));
    }

    private static long remainingUntil(long deadlineAtMs) {
        return Math.max(0L, deadlineAtMs - System.currentTimeMillis());
    }

    private static void broadcastEvent(
            Context app, String requestId, String event) {
        broadcastEvent(app, requestId, event, "", false, false, false);
    }

    private static void broadcastEvent(
            Context app,
            String requestId,
            String event,
            String phoneNodeId,
            boolean targetStart,
            boolean exactStarted,
            boolean hasError) {
        app.sendBroadcast(new Intent(ACTION_EVENT)
                .setPackage(app.getPackageName())
                .putExtra(EXTRA_REQUEST_ID, requestId == null ? "" : requestId.trim())
                .putExtra(EXTRA_EVENT, event)
                .putExtra(EXTRA_PHONE_NODE_ID,
                        phoneNodeId == null ? "" : phoneNodeId.trim())
                .putExtra(EXTRA_TARGET_START, targetStart)
                .putExtra(EXTRA_EXACT_STARTED, exactStarted)
                .putExtra(EXTRA_HAS_ERROR, hasError));
    }
}
