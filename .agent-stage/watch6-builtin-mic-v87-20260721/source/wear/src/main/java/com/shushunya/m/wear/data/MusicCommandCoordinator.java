package com.shushunya.m.wear.data;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import com.google.android.gms.wearable.Node;
import com.google.android.gms.wearable.Wearable;
import com.shushunya.m.wear.control.MusicCommandForegroundService;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/** Durable, standalone MUSIC sender. Hydra never enters this coordinator. */
public final class MusicCommandCoordinator {
    private static final Object LOCK = new Object();
    private static final AtomicLong GENERATION = new AtomicLong();

    private MusicCommandCoordinator() {}

    public static boolean begin(Context context, WearProtocol.Request request) {
        Context app = context.getApplicationContext();
        MusicCommandState state = MusicCommandState.create(
                request, System.currentTimeMillis());
        if (state == null) return false;
        synchronized (LOCK) {
            if (!MusicCommandStore.replace(app, state)) return false;
            GENERATION.incrementAndGet();
        }
        try {
            MusicCommandForegroundService.startPending(app, state.requestId);
        } catch (RuntimeException ignored) {
            // A user-visible tap normally permits the FGS. Keep the bounded
            // in-process schedule as a best effort if an OEM rejects it.
        }
        resumeIfNeeded(app);
        return true;
    }

    /** Reconstructs the remaining schedule from device-protected state. */
    public static void resumeIfNeeded(Context context) {
        Context app = context.getApplicationContext();
        MusicCommandState state = MusicCommandStore.read(app);
        if (state == null) return;
        if (!ControllerStateStore.isMatchingPending(
                app, ControllerStateStore.Kind.MUSIC, state.requestId)) {
            MusicCommandStore.clearExact(app, state.requestId);
            MusicCommandForegroundService.stop(app);
            return;
        }
        long now = System.currentTimeMillis();
        if (!MusicRetryPolicy.isAlive(state.startedAtMs, state.deadlineAtMs, now)) {
            failExact(app, state.requestId);
            return;
        }
        long generation = GENERATION.incrementAndGet();
        scheduleAttempt(app, state, generation, now);
        new Handler(Looper.getMainLooper()).postDelayed(
                () -> timeoutIfCurrent(app, state.requestId, generation),
                Math.max(1L, state.deadlineAtMs - now));
    }

    /** Checks exact source/correlation/semantics before state mutation. */
    public static boolean acceptsExactSemanticAck(
            Context context,
            String sourceNodeId,
            String requestId,
            String musicState,
            boolean exactErrorField) {
        MusicCommandState pending = MusicCommandStore.read(context);
        return pending != null && MusicAckPolicy.accepts(
                pending.requestId,
                pending.phoneNodeId,
                requestId,
                sourceNodeId,
                musicState,
                exactErrorField);
    }

    /** Called only after ControllerStateStore applied the exact /state reply. */
    public static boolean completeExactAfterState(
            Context context,
            String sourceNodeId,
            String requestId,
            String musicState,
            boolean exactErrorField,
            boolean hasError) {
        Context app = context.getApplicationContext();
        synchronized (LOCK) {
            MusicCommandState pending = MusicCommandStore.read(app);
            if (pending == null || !MusicAckPolicy.accepts(
                    pending.requestId,
                    pending.phoneNodeId,
                    requestId,
                    sourceNodeId,
                    musicState,
                    exactErrorField)) return false;
            if (!MusicCommandStore.clearExact(app, pending.requestId)) return false;
            GENERATION.incrementAndGet();
        }
        MusicCommandForegroundService.stop(app);
        return true;
    }

    public static String pendingRequestId(Context context) {
        MusicCommandState state = MusicCommandStore.read(context);
        return state == null ? "" : state.requestId;
    }

    private static void scheduleAttempt(
            Context app,
            MusicCommandState state,
            long generation,
            long nowMs) {
        if (state.nextAttemptIndex >= MusicRetryPolicy.attemptCount()) return;
        long delay = MusicRetryPolicy.delayUntilAttempt(
                state.startedAtMs, state.nextAttemptIndex, nowMs);
        if (delay < 0L) return;
        new Handler(Looper.getMainLooper()).postDelayed(
                () -> runAttempt(
                        app, state.requestId, state.nextAttemptIndex, generation),
                delay);
    }

    private static void runAttempt(
            Context app,
            String requestId,
            int attemptIndex,
            long generation) {
        if (generation != GENERATION.get()) return;
        MusicCommandState claimed = MusicCommandStore.claimAttempt(
                app, requestId, attemptIndex);
        if (claimed == null) return;
        long now = System.currentTimeMillis();
        if (!MusicRetryPolicy.isAlive(claimed.startedAtMs, claimed.deadlineAtMs, now)) {
            failExact(app, requestId);
            return;
        }
        MusicCommandState remaining = MusicCommandStore.read(app);
        if (remaining != null) scheduleAttempt(app, remaining, generation, now);
        resolveTargetAndSend(app, claimed, generation);
    }

    private static void resolveTargetAndSend(
            Context app,
            MusicCommandState state,
            long generation) {
        String selected = state.phoneNodeId;
        if (selected.isEmpty()) selected = ControlPhoneTargetStore.selectedNodeId(app);
        if (!selected.isEmpty()) {
            MusicCommandState bound = bindExactTarget(app, state.requestId, selected);
            if (bound != null) sendExact(app, bound, generation);
            return;
        }

        Wearable.getNodeClient(app).getConnectedNodes()
                .addOnSuccessListener(nodes -> {
                    if (generation != GENERATION.get()) return;
                    List<MusicTargetPolicy.Candidate> candidates = new ArrayList<>();
                    for (Node node : nodes) {
                        candidates.add(new MusicTargetPolicy.Candidate(
                                node.getId(), node.isNearby()));
                    }
                    String target = MusicTargetPolicy.selectOneNearby(candidates);
                    if (target.isEmpty()) return;
                    MusicCommandState bound = bindExactTarget(
                            app, state.requestId, target);
                    if (bound != null) sendExact(app, bound, generation);
                });
    }

    private static MusicCommandState bindExactTarget(
            Context app,
            String requestId,
            String phoneNodeId) {
        synchronized (LOCK) {
            MusicCommandState current = MusicCommandStore.read(app);
            if (current == null || !current.requestId.equals(requestId)) return null;
            if (!ControlPhoneTargetStore.rememberExact(app, phoneNodeId)) return null;
            return MusicCommandStore.bindPhoneNode(app, requestId, phoneNodeId);
        }
    }

    private static void sendExact(
            Context app,
            MusicCommandState state,
            long generation) {
        if (generation != GENERATION.get()) return;
        WearMessageSender.sendToTarget(
                app,
                state.phoneNodeId,
                WearProtocol.PATH_MUSIC_TOGGLE,
                state.jsonPayload)
                .addOnCompleteListener(task -> {
                    if (generation != GENERATION.get()
                            || !task.isSuccessful()
                            || !Boolean.TRUE.equals(task.getResult())) return;
                    MusicCommandState current = MusicCommandStore.read(app);
                    if (current == null
                            || !current.requestId.equals(state.requestId)
                            || !current.phoneNodeId.equals(state.phoneNodeId)) return;
                    if (ControllerStateStore.markTransport(
                            app,
                            ControllerStateStore.Kind.MUSIC,
                            state.requestId,
                            true)) {
                        ComplicationRefresh.request(
                                app, ControllerStateStore.Kind.MUSIC);
                    }
                });
    }

    private static void timeoutIfCurrent(
            Context app,
            String requestId,
            long generation) {
        if (generation != GENERATION.get()) return;
        MusicCommandState state = MusicCommandStore.read(app);
        if (state == null || !state.requestId.equals(requestId)) return;
        if (System.currentTimeMillis() < state.deadlineAtMs) return;
        failExact(app, requestId);
    }

    private static void failExact(Context app, String requestId) {
        boolean owned;
        synchronized (LOCK) {
            owned = MusicCommandStore.clearExact(app, requestId);
            if (owned) GENERATION.incrementAndGet();
        }
        if (!owned) return;
        boolean current = ControllerStateStore.markTransport(
                app,
                ControllerStateStore.Kind.MUSIC,
                requestId,
                false);
        if (current) {
            ComplicationRefresh.request(app, ControllerStateStore.Kind.MUSIC);
        }
        MusicCommandForegroundService.stop(app);
    }
}
