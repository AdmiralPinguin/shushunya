package com.shushunya.m.wear.control;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;

import com.shushunya.m.wear.data.ComplicationRefresh;
import com.shushunya.m.wear.data.ControllerStateStore;
import com.shushunya.m.wear.data.MusicCommandCoordinator;
import com.shushunya.m.wear.data.MusicRetryPolicy;
import com.shushunya.m.wear.data.WearMessageSender;
import com.shushunya.m.wear.data.WearProtocol;

import java.util.concurrent.atomic.AtomicBoolean;

public final class WearActionReceiver extends BroadcastReceiver {
    private static final long DELIVERY_TIMEOUT_MS = 8_000L;
    private static final ControllerStateStore.Kind[] MAGIC_KINDS = {
            ControllerStateStore.Kind.LIVE,
            ControllerStateStore.Kind.MUSIC
    };
    public static final String ACTION_LIVE_TOGGLE =
            "com.shushunya.m.wear.action.LIVE_TOGGLE";
    public static final String ACTION_MUSIC_TOGGLE =
            "com.shushunya.m.wear.action.MUSIC_TOGGLE";
    public static final String ACTION_MAGIC_TOGGLE =
            "com.shushunya.m.wear.action.MAGIC_TOGGLE";

    @Override
    public void onReceive(Context context, Intent intent) {
        PowerConfMode.enforce(context);
        String action = intent == null ? null : intent.getAction();
        ControllerStateStore.Kind[] kinds;
        String path;
        if (ACTION_LIVE_TOGGLE.equals(action)) {
            kinds = new ControllerStateStore.Kind[] { ControllerStateStore.Kind.LIVE };
            path = WearProtocol.PATH_LIVE_TOGGLE;
        } else if (ACTION_MUSIC_TOGGLE.equals(action)) {
            kinds = new ControllerStateStore.Kind[] { ControllerStateStore.Kind.MUSIC };
            path = WearProtocol.PATH_MUSIC_TOGGLE;
        } else if (ACTION_MAGIC_TOGGLE.equals(action)) {
            kinds = MAGIC_KINDS;
            path = WearProtocol.PATH_MAGIC_TOGGLE;
        } else {
            return;
        }

        boolean accepted = kinds.length == 1
                ? ControllerStateStore.acceptTap(context, kinds[0])
                : ControllerStateStore.acceptMagicTap(context);
        if (!accepted) return;
        Haptics.tick(context);

        WearProtocol.Request request = WearProtocol.newRequest();
        for (ControllerStateStore.Kind kind : kinds) {
            if (kind == ControllerStateStore.Kind.MUSIC
                    && kinds.length == 1) {
                ControllerStateStore.markPending(
                        context, kind, request.id, MusicRetryPolicy.BUDGET_MS);
            } else {
                ControllerStateStore.markPending(context, kind, request.id);
            }
            ComplicationRefresh.request(context, kind);
        }

        // Standalone MUSIC owns its own durable outbox and short process
        // anchor. Combined Hydra continues through its existing path below.
        if (kinds.length == 1 && kinds[0] == ControllerStateStore.Kind.MUSIC) {
            if (!MusicCommandCoordinator.begin(context, request)) {
                boolean current = ControllerStateStore.markTransport(
                        context,
                        ControllerStateStore.Kind.MUSIC,
                        request.id,
                        false);
                if (current) {
                    ComplicationRefresh.request(
                            context, ControllerStateStore.Kind.MUSIC);
                    Haptics.failure(context);
                }
            }
            return;
        }

        PendingResult pendingResult = goAsync();
        Handler handler = new Handler(Looper.getMainLooper());
        AtomicBoolean completed = new AtomicBoolean(false);
        Runnable timeout = () -> {
            if (!completed.compareAndSet(false, true)) return;
            boolean anyCurrent = false;
            for (ControllerStateStore.Kind kind : kinds) {
                boolean current = ControllerStateStore.markTransport(
                        context, kind, request.id, false);
                anyCurrent |= current;
                if (current) ComplicationRefresh.request(context, kind);
            }
            if (anyCurrent) {
                Haptics.failure(context);
            }
            pendingResult.finish();
        };
        handler.postDelayed(timeout, DELIVERY_TIMEOUT_MS);
        WearMessageSender.sendToNearby(context, path, request.json)
                .addOnCompleteListener(task -> {
                    if (!completed.compareAndSet(false, true)) return;
                    handler.removeCallbacks(timeout);
                    boolean sent = task.isSuccessful() && Boolean.TRUE.equals(task.getResult());
                    boolean anyCurrent = false;
                    for (ControllerStateStore.Kind kind : kinds) {
                        boolean current = ControllerStateStore.markTransport(
                                context, kind, request.id, sent);
                        anyCurrent |= current;
                        if (!current) continue;
                        if (!sent || kind != ControllerStateStore.Kind.LIVE
                                || ControllerStateStore.snapshot(
                                context, kind).livePhase == ControllerStateStore.LivePhase.NONE) {
                            ComplicationRefresh.request(context, kind);
                        }
                    }
                    if (anyCurrent && !sent) {
                        Haptics.failure(context);
                    }
                    pendingResult.finish();
                });
    }

}
