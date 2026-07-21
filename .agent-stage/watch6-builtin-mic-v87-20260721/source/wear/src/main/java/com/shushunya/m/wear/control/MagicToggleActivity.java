package com.shushunya.m.wear.control;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.view.WindowManager;
import android.widget.Toast;

import com.shushunya.m.wear.audio.WearMicForegroundService;
import com.shushunya.m.wear.data.ComplicationRefresh;
import com.shushunya.m.wear.data.ControllerStateStore;
import com.shushunya.m.wear.data.WearProtocol;

import java.util.concurrent.atomic.AtomicBoolean;

/** Launcher target for the watch's configurable double-press Home shortcut. */
public final class MagicToggleActivity extends Activity {
    private static final int REQUEST_CAPTURE_PERMISSIONS = 821;
    private static final ControllerStateStore.Kind[] MAGIC_KINDS = {
            ControllerStateStore.Kind.LIVE,
            ControllerStateStore.Kind.MUSIC
    };

    private boolean dispatched;
    private boolean initialized;
    private boolean awaitingCapturePermissions;
    private boolean coordinatorReceiverRegistered;
    private boolean activityResumed;
    // Keeps a second physical tap from replacing the still-running, same-id
    // one-tap recovery sequence in this singleTop Activity.
    private final AtomicBoolean remoteBridgePending = new AtomicBoolean(false);
    private String activeRequestId = "";
    private String dispatchAction = WearActionReceiver.ACTION_MAGIC_TOGGLE;
    private MagicDispatchPolicy.Route dispatchRoute =
            MagicDispatchPolicy.Route.DATA_LAYER_TOGGLE;

    private final BroadcastReceiver coordinatorReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (intent == null
                    || !DurableMagicWakeCoordinator.ACTION_EVENT.equals(
                            intent.getAction())) return;
            String requestId = intent.getStringExtra(
                    DurableMagicWakeCoordinator.EXTRA_REQUEST_ID);
            if (activeRequestId.isEmpty() || !activeRequestId.equals(requestId)) return;
            String event = intent.getStringExtra(
                    DurableMagicWakeCoordinator.EXTRA_EVENT);
            if (DurableMagicWakeCoordinator.EVENT_ACCEPTED.equals(event)) {
                boolean targetStart = intent.getBooleanExtra(
                        DurableMagicWakeCoordinator.EXTRA_TARGET_START, false);
                String phoneNodeId = intent.getStringExtra(
                        DurableMagicWakeCoordinator.EXTRA_PHONE_NODE_ID);
                VisibleMicLaunchPolicy.Action action = VisibleMicLaunchPolicy.decide(
                        targetStart,
                        activityResumed,
                        isFinishing(),
                        isDestroyed());
                if (action == VisibleMicLaunchPolicy.Action.DEFER) {
                    // The ACK direction remains durable. onResume() will replay it
                    // when Android's microphone while-in-use requirement is met.
                    return;
                }
                boolean applied;
                if (action == VisibleMicLaunchPolicy.Action.START) {
                    applied = startWatchMicUplinkExact(requestId, phoneNodeId);
                    if (!applied) {
                        remoteBridgePending.set(false);
                        finishAndRemoveTask();
                    }
                } else {
                    // SET(false) is only an accepted intent here. The phone owns
                    // the raw archive and sends the separately-correlated drain
                    // request; stopping locally would race that owner and turn a
                    // graceful archive FINISH into a bare EOF.
                    applied = true;
                }
                if (applied && !DurableMagicWakeCoordinator.markAcceptedActionConsumedExact(
                        MagicToggleActivity.this,
                        requestId,
                        phoneNodeId,
                        targetStart)) {
                    if (targetStart) {
                        WearMicForegroundService.stop(MagicToggleActivity.this);
                    }
                    remoteBridgePending.set(false);
                    finishAndRemoveTask();
                }
                // Keep the real user-visible bridge alive: Samsung may reject
                // application-context RemoteActivity retries after it finishes.
                return;
            }
            boolean exactStarted = intent.getBooleanExtra(
                    DurableMagicWakeCoordinator.EXTRA_EXACT_STARTED, false);
            boolean hasError = intent.getBooleanExtra(
                    DurableMagicWakeCoordinator.EXTRA_HAS_ERROR, false);
            boolean targetStart = intent.getBooleanExtra(
                    DurableMagicWakeCoordinator.EXTRA_TARGET_START, false);
            if (DurableMagicWakeCoordinator.EVENT_FAILED.equals(event)
                    || hasError
                    || (DurableMagicWakeCoordinator.EVENT_TERMINAL.equals(event)
                    && !exactStarted)) {
                // Failed SET(true) must roll back the capture it just started.
                // Failed SET(false) must retain the pre-existing capture.
                if (targetStart) {
                    WearMicForegroundService.stop(MagicToggleActivity.this);
                }
            }
            remoteBridgePending.set(false);
            finishAndRemoveTask();
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // RemoteInteractionsApi accepts a request only after a real user-visible
        // Watch action. Keep this bridge awake until exact terminal/failure.
        setShowWhenLocked(true);
        setTurnScreenOn(true);
        getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                        | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                        | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        PowerConfMode.enforce(this);
    }

    private boolean initializeAfterCapturePermissions() {
        if (initialized) return true;
        initialized = true;
        MagicWakeCoordinatorState recovered = MagicWakeCoordinatorStore.read(this);
        if (recovered != null && ControllerStateStore.isMatchingPending(
                this, ControllerStateStore.Kind.LIVE, recovered.requestId)) {
            activeRequestId = recovered.requestId;
            dispatched = true;
            remoteBridgePending.set(true);
            registerCoordinatorReceiver();
            try {
                MagicCommandForegroundService.startPending(this, recovered.requestId);
            } catch (RuntimeException error) {
                DurableMagicWakeCoordinator.abortStartExact(
                        this,
                        recovered.requestId,
                        "Не удалось восстановить команду переводчика");
                remoteBridgePending.set(false);
                finishAndRemoveTask();
                return false;
            }
            DurableMagicWakeCoordinator.resumeIfNeeded(this);
        }
        return true;
    }

    @Override
    protected void onResume() {
        super.onResume();
        activityResumed = true;
        if (!ensureCapturePermissions()) return;
        if (!initializeAfterCapturePermissions()) return;
        if (!dispatched) {
            handleVisibleCommand();
        } else {
            DurableMagicWakeCoordinator.replayAcceptedActionIfNeeded(this);
        }
    }

    @Override
    protected void onPause() {
        activityResumed = false;
        super.onPause();
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_CAPTURE_PERMISSIONS) return;
        awaitingCapturePermissions = false;
        boolean granted = checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
        if (!granted) {
            Haptics.failure(this);
            Toast.makeText(
                    this,
                    "Разреши микрофон для встроенного микрофона Watch6",
                    Toast.LENGTH_LONG).show();
            WearMicForegroundService.stop(this);
            finishAndRemoveTask();
            return;
        }
        if (activityResumed && initializeAfterCapturePermissions()) {
            if (!dispatched) handleVisibleCommand();
            else DurableMagicWakeCoordinator.replayAcceptedActionIfNeeded(this);
        }
    }

    private boolean ensureCapturePermissions() {
        boolean microphone = checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
        if (microphone) return true;
        if (!awaitingCapturePermissions) {
            awaitingCapturePermissions = true;
            requestPermissions(
                    new String[] {
                            Manifest.permission.RECORD_AUDIO,
                            Manifest.permission.POST_NOTIFICATIONS
                    },
                    REQUEST_CAPTURE_PERMISSIONS);
        }
        return false;
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (remoteBridgePending.get()) return;
        setIntent(intent);
        dispatched = false;
    }

    private void handleVisibleCommand() {
        dispatchAction = WearActionReceiver.ACTION_LIVE_TOGGLE.equals(getIntent().getAction())
                ? WearActionReceiver.ACTION_LIVE_TOGGLE
                : WearActionReceiver.ACTION_MAGIC_TOGGLE;
        boolean magicAction = WearActionReceiver.ACTION_MAGIC_TOGGLE.equals(dispatchAction);
        dispatchRoute = MagicDispatchPolicy.route(magicAction);
        if (dispatchRoute == MagicDispatchPolicy.Route.DATA_LAYER_TOGGLE) {
            dispatchAndFinish();
            return;
        }
        if (!ControllerStateStore.acceptMagicTap(this)) {
            // Pending and the post-start settle window are strict no-ops.
            finishAndRemoveTask();
            return;
        }
        // UUID, pending correlation, phase and retry authority are one
        // device-protected commit before the short-lived command keepalive.
        prepareRemotePhoneMagicToggle();
    }

    private boolean prepareRemotePhoneMagicToggle() {
        dispatched = true;
        remoteBridgePending.set(true);
        WearProtocol.Request request = WearProtocol.newRequest();
        activeRequestId = request.id;
        registerCoordinatorReceiver();
        if (DurableMagicWakeCoordinator.begin(
                this, request.id, request.issuedAtMs)) {
            for (ControllerStateStore.Kind kind : MAGIC_KINDS) {
                ComplicationRefresh.request(this, kind);
            }
            return true;
        }

        remoteBridgePending.set(false);
        Haptics.failure(this);
        Toast.makeText(
                this,
                "Не удалось сохранить команду переводчика",
                Toast.LENGTH_LONG).show();
        finishAndRemoveTask();
        return false;
    }

    /** Runs only from this still-visible Activity after the exact phone ACK. */
    private boolean startWatchMicUplinkExact(String requestId, String phoneNodeId) {
        String request = requestId == null ? "" : requestId.trim();
        String node = phoneNodeId == null ? "" : phoneNodeId.trim();
        if (request.isEmpty() || node.isEmpty()
                || !ensureCapturePermissions()) {
            Haptics.failure(this);
            Toast.makeText(
                    this,
                    "Телефон не подтвердил uplink микрофона Watch6",
                    Toast.LENGTH_LONG).show();
            WearMicForegroundService.stop(this);
            DurableMagicWakeCoordinator.abortStartExact(
                    this,
                    request,
                    "Телефон не подтвердил uplink микрофона Watch6");
            return false;
        }
        try {
            WearMicForegroundService.startExact(this, request, node);
            return true;
        } catch (RuntimeException error) {
            Haptics.failure(this);
            Toast.makeText(
                    this,
                    "Android не запустил микрофон Watch6 в фоне",
                    Toast.LENGTH_LONG).show();
            WearMicForegroundService.stop(this);
            DurableMagicWakeCoordinator.abortStartExact(
                    this,
                    request,
                    "Android не разрешил запустить микрофон Watch6 в фоне");
            return false;
        }
    }

    private void registerCoordinatorReceiver() {
        if (coordinatorReceiverRegistered) return;
        registerReceiver(
                coordinatorReceiver,
                new IntentFilter(DurableMagicWakeCoordinator.ACTION_EVENT),
                Context.RECEIVER_NOT_EXPORTED);
        coordinatorReceiverRegistered = true;
    }

    private void dispatchAndFinish() {
        if (!dispatched) {
            dispatched = true;
            sendBroadcast(new Intent(this, WearActionReceiver.class)
                    .setAction(dispatchAction));
        }
        finishAndRemoveTask();
    }

    @Override
    protected void onDestroy() {
        if (coordinatorReceiverRegistered) {
            try {
                unregisterReceiver(coordinatorReceiver);
            } catch (IllegalArgumentException ignored) {
                // Activity teardown can race a coordinator event.
            }
            coordinatorReceiverRegistered = false;
        }
        super.onDestroy();
    }
}
