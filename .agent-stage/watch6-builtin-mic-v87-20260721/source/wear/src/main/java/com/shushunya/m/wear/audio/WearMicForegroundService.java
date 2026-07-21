package com.shushunya.m.wear.audio;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTimestamp;
import android.media.MediaRecorder;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import com.google.android.gms.tasks.Tasks;
import com.google.android.gms.tasks.Task;
import com.google.android.gms.wearable.ChannelClient;
import com.google.android.gms.wearable.Node;
import com.google.android.gms.wearable.Wearable;
import com.shushunya.m.R;
import com.shushunya.m.wear.control.MagicToggleActivity;
import com.shushunya.m.wear.control.WearActionReceiver;
import com.shushunya.m.wear.data.ComplicationRefresh;
import com.shushunya.m.wear.data.ControllerStateStore;
import com.shushunya.m.wear.data.WatchStartupFailureOutbox;

import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * User-started, fail-closed Galaxy Watch built-in microphone uplink. Capture
 * never substitutes a Bluetooth, USB, or other external input.
 */
public final class WearMicForegroundService extends Service {
    public static final String ACTION_START = "com.shushunya.m.wear.audio.START";
    public static final String ACTION_STOP = "com.shushunya.m.wear.audio.STOP";
    public static final String EXTRA_PHONE_NODE_ID = "phone_node_id";
    public static final String EXTRA_REQUEST_ID = "request_id";

    private static final String CHANNEL_ID = "shushunya_watch_microphone";
    private static final String FAILURE_CHANNEL_ID = "shushunya_watch_microphone_attention";
    private static final String TAG = "ShushunyaWatchMic";
    private static final int NOTIFICATION_ID = 1861;
    private static final int FAILURE_NOTIFICATION_ID = 1863;
    private static final int MAX_QUEUED_FRAMES = 12; // bounded 240 ms
    private static final int TIMESTAMP_STARTUP_FRAMES = 5; // bounded 100 ms
    private static final long ROUTE_TIMEOUT_MS = 6_000L;
    private static final long CHANNEL_TIMEOUT_MS = 12_000L;
    private static final long DEFAULT_GRACEFUL_DRAIN_TIMEOUT_MS = 3_000L;
    private static final long MAX_GRACEFUL_DRAIN_TIMEOUT_MS =
            WearAudioLifecycleProtocol.MAX_DRAIN_TIMEOUT_MS;
    // Covers the phone's byte-identical binding retries at 0/350/1000 ms while
    // still leaving the full terminal ACK schedule inside its 8 s drain gate.
    private static final long PHONE_BINDING_WAIT_MS = 1_500L;
    private static final long TERMINAL_ACK_TIMEOUT_MS =
            CrossDeviceDrainBudgetPolicy.TERMINAL_ACK_MS;
    private static final long WRITER_POLL_MS = 50L;
    private static final long TELEMETRY_NOTIFICATION_INTERVAL_FRAMES = 250L;
    private static final AtomicBoolean CAPTURE_SERVICE_ACTIVE = new AtomicBoolean(false);
    private static final AtomicReference<WearMicForegroundService> ACTIVE_INSTANCE =
            new AtomicReference<>();
    private static final Object LAUNCH_LOCK = new Object();
    private static boolean foregroundLaunchPending;
    private static boolean stopBeforeForegroundRequested;

    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicBoolean stopBeforeStreamingRequested = new AtomicBoolean(false);
    private final AtomicBoolean gracefulStopRequested = new AtomicBoolean(false);
    private final AtomicBoolean hardAbortRequested = new AtomicBoolean(false);
    private final AtomicLong streamGeneration = new AtomicLong(0L);
    private final AtomicLong activeStreamSessionId = new AtomicLong(0L);
    private final AtomicLong activeLastFlushedSequence = new AtomicLong(-1L);
    private final AtomicReference<PhoneStreamBinding> activePhoneBinding =
            new AtomicReference<>();
    private final AtomicReference<DrainRequestOwner> activeDrainOwner =
            new AtomicReference<>();
    private final WearMicTelemetry telemetry = new WearMicTelemetry();
    private volatile Thread worker;
    private volatile SessionResources activeResources;
    private volatile String activePhoneNodeId = "";
    private volatile String activeRequestId = "";
    private volatile long requestedDrainTimeoutMs = DEFAULT_GRACEFUL_DRAIN_TIMEOUT_MS;
    private volatile long activeDrainAcceptedAtElapsedMs;
    private volatile long lastTelemetryNotificationFrame;
    private PowerManager.WakeLock wakeLock;

    /** Process-local signal only; it never starts microphone capture by itself. */
    public static boolean isCaptureServiceActive() {
        return CAPTURE_SERVICE_ACTIVE.get();
    }

    /**
     * Marks the foreground launch before asking Android to create the service.
     * A phone-side rejection can arrive on another Wear listener thread before
     * {@link #onStartCommand(Intent, int, int)} gets its first foreground call.
     */
    public static void startExact(Context context, String requestId, String phoneNodeId) {
        if (context == null) throw new IllegalArgumentException("context is required");
        synchronized (LAUNCH_LOCK) {
            foregroundLaunchPending = true;
            stopBeforeForegroundRequested = false;
        }
        try {
            context.startForegroundService(new Intent(context, WearMicForegroundService.class)
                    .setAction(ACTION_START)
                    .putExtra(EXTRA_REQUEST_ID, requestId)
                    .putExtra(EXTRA_PHONE_NODE_ID, phoneNodeId));
        } catch (RuntimeException error) {
            synchronized (LAUNCH_LOCK) {
                foregroundLaunchPending = false;
                stopBeforeForegroundRequested = false;
            }
            throw error;
        }
    }

    public static void stop(Context context) {
        if (context == null) return;
        synchronized (LAUNCH_LOCK) {
            if (foregroundLaunchPending) {
                // Do not call stopService() here. Wear OS treats that as an FGS
                // contract violation when onStartCommand has not run yet.
                stopBeforeForegroundRequested = true;
                return;
            }
        }
        WearMicForegroundService active = ACTIVE_INSTANCE.get();
        if (active != null) {
            active.requestGracefulStop("local/watch-mic lifecycle stop", -1L, null);
            return;
        }
        // stopService() never delivers ACTION_STOP. It is only a cleanup fallback
        // when this process has no live service instance and therefore no PCM to drain.
        Context app = context.getApplicationContext();
        app.stopService(new Intent(app, WearMicForegroundService.class));
    }

    public static boolean acceptPhoneBinding(
            String sourceNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId) {
        WearMicForegroundService active = ACTIVE_INSTANCE.get();
        return active != null && active.acceptPhoneBindingExact(
                sourceNodeId, requestId, captureGroupId, runGeneration, sessionId);
    }

    public static boolean requestPhoneDrain(
            String sourceNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            long timeoutMs) {
        WearMicForegroundService active = ACTIVE_INSTANCE.get();
        return active != null && active.requestPhoneDrainExact(
                sourceNodeId,
                requestId,
                captureGroupId,
                runGeneration,
                sessionId,
                timeoutMs);
    }

    public static boolean acceptTerminalAck(
            String sourceNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            String disposition,
            long acceptedLastSequence,
            String error,
            long ackAtMs) {
        WearMicForegroundService active = ACTIVE_INSTANCE.get();
        return active != null && active.acceptTerminalAckExact(
                sourceNodeId,
                requestId,
                captureGroupId,
                runGeneration,
                sessionId,
                disposition,
                acceptedLastSequence,
                error,
                ackAtMs);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        ACTIVE_INSTANCE.set(this);
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? "" : clean(intent.getAction());
        if (ACTION_STOP.equals(action)) {
            requestGracefulStop("foreground STOP command", -1L, null);
            return START_NOT_STICKY;
        }
        if (!ACTION_START.equals(action)) {
            Log.w(TAG, "Rejecting missing/unknown service action=" + action);
            hardAbortStreaming(true, "unknown service action");
            stopSelf();
            return START_NOT_STICKY;
        }
        cancelFailureNotification();

        String phoneNodeId = bounded(intent.getStringExtra(EXTRA_PHONE_NODE_ID), 256);
        String requestId = bounded(intent.getStringExtra(EXTRA_REQUEST_ID), 256);
        try {
            startConnectedForeground("Готовлю встроенный микрофон Watch6…");
        } catch (RuntimeException foregroundError) {
            clearPendingForegroundLaunch();
            Log.e(TAG, "Could not enter connected-device foreground", foregroundError);
            failBeforeStart(
                    phoneNodeId, requestId,
                    "CONNECTED_DEVICE_FGS",
                    "Android rejected connected-device foreground");
            return START_NOT_STICKY;
        }
        if (consumeStopRequestedBeforeForeground()) {
            hardAbortStreaming(true, "phone cancelled while Watch FGS was launching");
            stopSelfResult(startId);
            return START_NOT_STICKY;
        }
        if (phoneNodeId.isEmpty() || requestId.isEmpty()) {
            failBeforeStart(
                    phoneNodeId, requestId,
                    "INVALID_COMMAND", "Нет точного phone node/request");
            return START_NOT_STICKY;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            failBeforeStart(
                    phoneNodeId, requestId,
                    "MIC_PERMISSION", "Нет разрешения микрофона");
            return START_NOT_STICKY;
        }
        try {
            startCaptureForeground("Включаю встроенный микрофон Watch6…");
        } catch (RuntimeException foregroundError) {
            Log.e(TAG, "Could not promote microphone foreground", foregroundError);
            failBeforeStart(
                    phoneNodeId, requestId,
                    "MICROPHONE_FGS", "Android запретил microphone foreground");
            return START_NOT_STICKY;
        }

        if (stopBeforeStreamingRequested.getAndSet(false)) {
            hardAbortStreaming(true, "phone cancelled before Watch capture started");
            stopSelfResult(startId);
            return START_NOT_STICKY;
        }

        synchronized (this) {
            if (running.get()
                    && !gracefulStopRequested.get()
                    && phoneNodeId.equals(activePhoneNodeId)
                    && requestId.equals(activeRequestId)) {
                return START_NOT_STICKY;
            }
        }
        hardAbortStreaming(false, "replacement START command");
        telemetry.reset();
        lastTelemetryNotificationFrame = 0L;
        publishTelemetry(telemetry.advance(WearMicTelemetry.Stage.PERMISSION_GRANTED), true);
        publishTelemetry(telemetry.advance(WearMicTelemetry.Stage.FOREGROUND_SERVICE), true);
        startStreaming(phoneNodeId, requestId);
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        ACTIVE_INSTANCE.compareAndSet(this, null);
        hardAbortStreaming(true, "service destroyed");
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void failBeforeStart(
            String phoneNodeId, String requestId, String code, String status) {
        publishStartupFailureToPhone(phoneNodeId, requestId, code, status);
        publishTelemetry(telemetry.fail(code, null), true);
        updateNotification(status);
        showFailureNotification(status);
        hardAbortStreaming(true, "failure before capture start");
        stopSelf();
    }

    private synchronized void startStreaming(String phoneNodeId, String requestId) {
        if (!running.compareAndSet(false, true)) return;
        gracefulStopRequested.set(false);
        hardAbortRequested.set(false);
        activeStreamSessionId.set(0L);
        activeLastFlushedSequence.set(-1L);
        activePhoneBinding.set(null);
        activeDrainOwner.set(null);
        WearAudioTerminalAckRegistry.clear();
        requestedDrainTimeoutMs = DEFAULT_GRACEFUL_DRAIN_TIMEOUT_MS;
        activeDrainAcceptedAtElapsedMs = 0L;
        activePhoneNodeId = phoneNodeId;
        activeRequestId = requestId;
        CAPTURE_SERVICE_ACTIVE.set(true);
        long generation = streamGeneration.incrementAndGet();
        acquireWakeLock();
        worker = new Thread(
                () -> runExactSession(generation, phoneNodeId, requestId),
                "watch-mic-uplink");
        worker.start();
    }

    private synchronized void hardAbortStreaming(boolean removeForeground, String reason) {
        hardAbortRequested.set(true);
        gracefulStopRequested.set(false);
        running.set(false);
        CAPTURE_SERVICE_ACTIVE.set(false);
        streamGeneration.incrementAndGet();
        SessionResources resources = activeResources;
        activeResources = null;
        if (resources != null) resources.close();
        Thread thread = worker;
        worker = null;
        if (thread != null && thread != Thread.currentThread()) thread.interrupt();
        activePhoneNodeId = "";
        activeRequestId = "";
        activeStreamSessionId.set(0L);
        activeLastFlushedSequence.set(-1L);
        activePhoneBinding.set(null);
        activeDrainOwner.set(null);
        activeDrainAcceptedAtElapsedMs = 0L;
        WearAudioTerminalAckRegistry.clear();
        releaseWakeLock();
        ControllerStateStore.updateWatchMicrophone(this, false, "Микрофон Watch6 остановлен");
        ComplicationRefresh.request(this, ControllerStateStore.Kind.LIVE);
        if (removeForeground) stopForeground(STOP_FOREGROUND_REMOVE);
        if (!clean(reason).isEmpty()) Log.i(TAG, "Hard abort: " + reason);
    }

    private boolean requestGracefulStop(
            String reason,
            long expectedGeneration,
            DrainRequestOwner expectedDrainOwner) {
        SessionResources resources;
        Thread activeWorker;
        synchronized (this) {
            if (expectedGeneration >= 0L
                    && (streamGeneration.get() != expectedGeneration
                    || activeDrainOwner.get() != expectedDrainOwner)) {
                return false;
            }
            if (!running.get()) {
                stopBeforeStreamingRequested.set(true);
                CAPTURE_SERVICE_ACTIVE.set(false);
                stopForeground(STOP_FOREGROUND_REMOVE);
                stopSelf();
                return false;
            }
            if (!gracefulStopRequested.compareAndSet(false, true)) return true;
            resources = activeResources;
            activeWorker = worker;
            updateNotification("Останавливаю микрофон Watch6 без потери хвоста…");
        }
        if (resources != null) resources.requestProducerStop();
        // Before the producer starts there are no complete PCM frames to drain.
        // Interrupting the setup worker avoids waiting through route/channel timeouts.
        if ((resources == null || !resources.captureStarted())
                && activeWorker != null
                && activeWorker != Thread.currentThread()) {
            activeWorker.interrupt();
        }
        Log.i(TAG, "Graceful stop requested: " + clean(reason));
        return true;
    }

    private static void clearPendingForegroundLaunch() {
        synchronized (LAUNCH_LOCK) {
            foregroundLaunchPending = false;
            stopBeforeForegroundRequested = false;
        }
    }

    private static boolean consumeStopRequestedBeforeForeground() {
        synchronized (LAUNCH_LOCK) {
            boolean requested = stopBeforeForegroundRequested;
            foregroundLaunchPending = false;
            stopBeforeForegroundRequested = false;
            return requested;
        }
    }

    private synchronized boolean acceptPhoneBindingExact(
            String sourceNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId) {
        if (!running.get()
                || !activePhoneNodeId.equals(clean(sourceNodeId))
                || sessionId <= 0L
                || activeStreamSessionId.get() != sessionId) return false;
        final PhoneStreamBinding candidate;
        try {
            candidate = new PhoneStreamBinding(
                    sourceNodeId, requestId, captureGroupId, runGeneration, sessionId);
        } catch (IllegalArgumentException invalid) {
            return false;
        }
        PhoneStreamBinding existing = activePhoneBinding.get();
        if (existing != null
                && (!existing.phoneNodeId.equals(candidate.phoneNodeId)
                || !existing.requestId.equals(candidate.requestId)
                || !existing.captureGroupId.equals(candidate.captureGroupId)
                || existing.runGeneration != candidate.runGeneration)) {
            return false;
        }
        activePhoneBinding.set(candidate);
        Log.i(TAG, "Bound exact phone raw group=" + candidate.captureGroupId
                + " run=" + candidate.runGeneration
                + " session=" + candidate.sessionId);
        return true;
    }

    private boolean requestPhoneDrainExact(
            String sourceNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            long timeoutMs) {
        final DrainRequestOwner acceptedOwner;
        final long acceptedGeneration;
        synchronized (this) {
            if (!running.get()
                    || !activePhoneNodeId.equals(clean(sourceNodeId))
                    || !WearAudioLifecycleProtocol.validId(requestId)
                    || !WearAudioLifecycleProtocol.validId(captureGroupId)
                    || runGeneration <= 0L
                    || timeoutMs <= 0L
                    || timeoutMs > MAX_GRACEFUL_DRAIN_TIMEOUT_MS) return false;
            PhoneStreamBinding binding = activePhoneBinding.get();
            DrainRequestOwner owner = activeDrainOwner.get();
            DrainRequestOwner.Decision decision = DrainRequestOwner.decide(
                    owner,
                    binding,
                    sourceNodeId,
                    requestId,
                    captureGroupId,
                    runGeneration,
                    sessionId,
                    timeoutMs);
            if (decision == DrainRequestOwner.Decision.REJECT) return false;
            if (decision == DrainRequestOwner.Decision.ACCEPT_NEW) {
                activeDrainOwner.set(DrainRequestOwner.create(
                        binding, requestId, sessionId, timeoutMs));
                activeDrainAcceptedAtElapsedMs = SystemClock.elapsedRealtime();
            }
            requestedDrainTimeoutMs = Math.min(
                    CrossDeviceDrainBudgetPolicy.PCM_DRAIN_MS,
                    Math.min(MAX_GRACEFUL_DRAIN_TIMEOUT_MS, Math.max(1L, timeoutMs)));
            acceptedOwner = activeDrainOwner.get();
            acceptedGeneration = streamGeneration.get();
        }
        return requestGracefulStop(
                "exact phone drain request", acceptedGeneration, acceptedOwner);
    }

    private boolean acceptTerminalAckExact(
            String sourceNodeId,
            String requestId,
            String captureGroupId,
            long runGeneration,
            long sessionId,
            String disposition,
            long acceptedLastSequence,
            String error,
            long ackAtMs) {
        PhoneStreamBinding binding = activePhoneBinding.get();
        return WearAudioTerminalAckRegistry.record(
                binding,
                activeDrainOwner.get(),
                sourceNodeId,
                requestId,
                captureGroupId,
                runGeneration,
                sessionId,
                clean(disposition),
                acceptedLastSequence,
                error,
                ackAtMs);
    }

    private boolean isGenerationOwned(long generation) {
        return running.get()
                && streamGeneration.get() == generation
                && !hardAbortRequested.get();
    }

    private boolean isGenerationActive(long generation) {
        return isGenerationOwned(generation)
                && !gracefulStopRequested.get()
                && !Thread.currentThread().isInterrupted();
    }

    private void runExactSession(long generation, String phoneNodeId, String requestId) {
        SessionResources resources = null;
        boolean failed = false;
        try {
            awaitExactPhoneNode(phoneNodeId, generation);
            publishTelemetry(telemetry.advance(WearMicTelemetry.Stage.NEARBY_NODE), true);

            BuiltInCaptureSetup capture = openBuiltInCaptureWithStartupProof(generation);
            resources = capture.resources;
            ChannelWriter initialWriter = openFramedChannel(
                    resources,
                    phoneNodeId,
                    generation,
                    CHANNEL_TIMEOUT_MS,
                    false);
            publishTelemetry(telemetry.advance(WearMicTelemetry.Stage.CHANNEL_OPEN), true);
            publishTelemetry(telemetry.routeReady(
                    "Galaxy Watch6 · встроенный микрофон · VOICE_RECOGNITION · 16 kHz mono"),
                    true);
            WearMicTelemetry.FrameObservation startupObservation =
                    telemetry.captured(capture.startup.samples);
            if (!startupObservation.firstNonzero) {
                throw new IllegalStateException("Startup PCM proof lost before publication");
            }
            maybePublishFrameTelemetry(startupObservation.snapshot, true);
            Log.i(TAG, "Built-in Watch6 uplink ready request=" + requestId
                    + " phoneNode=" + phoneNodeId
                    + " inputId=" + capture.builtInInput.getId()
                    + " inputType=" + capture.builtInInput.getType()
                    + " sampleRate=" + capture.record.getSampleRate()
                    + " startupReacquisitions=" + capture.reacquisitions);

            StreamResult streamResult = streamFrames(
                    capture.record,
                    initialWriter,
                    resources,
                    capture.builtInInput,
                    phoneNodeId,
                    generation,
                    capture.startup);
            String disposition = GracefulDrainPolicy.terminalDisposition(
                    gracefulStopRequested.get(),
                    streamResult.lastFlushedSequence,
                    streamResult.droppedFrames,
                    false);
            if (WearAudioLifecycleProtocol.DISPOSITION_HARD_FAILURE.equals(disposition)) {
                failed = true;
                throw new IllegalStateException(
                        "Watch microphone stream ended without lossless graceful terminal");
            }
            // The complete PCM tail is already flushed and immutable here.
            // Release AudioRecord and the channel before waiting for the
            // phone's durable archive ACK.
            // Terminal delivery uses MessageClient and owns no audio resource.
            resources.close();
            boolean terminalConfirmed = publishTerminal(
                    generation,
                    disposition,
                    streamResult.lastFlushedSequence,
                    streamResult.droppedFrames,
                    "graceful queue drain complete");
            if (activePhoneBinding.get() != null && !terminalConfirmed) {
                Log.w(TAG, "Phone did not confirm exact graceful terminal before ACK timeout");
                failed = true;
                publishTelemetry(telemetry.fail(
                        "TERMINAL_ACK",
                        new IllegalStateException("exact graceful terminal ACK timeout")), true);
                showFailureNotification("Phone did not acknowledge exact graceful terminal");
            }
        } catch (InterruptedException interrupted) {
            // A phone drain can arrive in the narrow interval after the framed
            // channel is bound but before streamFrames starts. Preserve the
            // zero-frame contract instead of turning that valid STOP into a
            // silent bare EOF.
            if (streamGeneration.get() == generation
                    && gracefulStopRequested.get()
                    && activeDrainOwner.get() != null) {
                long lastSequence = activeLastFlushedSequence.get();
                long droppedFrames = telemetry.snapshot().droppedFrames;
                String disposition = GracefulDrainPolicy.terminalDisposition(
                        true, lastSequence, droppedFrames, false);
                if (WearAudioLifecycleProtocol.DISPOSITION_HARD_FAILURE.equals(disposition)) {
                    failed = true;
                }
                // Zero-frame STOP has no PCM tail, so it can release the exact
                // route immediately while the terminal retries continue over
                // MessageClient only.
                if (resources != null) resources.close();
                boolean confirmed = publishTerminal(
                        generation,
                        disposition,
                        lastSequence,
                        droppedFrames,
                        "graceful drain before first PCM frame");
                if (!confirmed) {
                    failed = true;
                    publishTelemetry(telemetry.fail(
                            "TERMINAL_ACK",
                            new IllegalStateException("zero-frame terminal ACK timeout")), true);
                }
            } else {
                Thread.currentThread().interrupt();
            }
        } catch (Exception error) {
            failed = true;
            if (streamGeneration.get() == generation) {
                // A proven route/capture failure is a hard abort: release the
                // microphone and channel immediately. The correlated
                // terminal retry uses MessageClient and does not require them.
                if (resources != null) resources.close();
                // Before a proven non-zero frame there is deliberately no
                // framed phone channel/session to terminate.
                if (activeStreamSessionId.get() > 0L) {
                    publishTerminal(
                            generation,
                            WearAudioLifecycleProtocol.DISPOSITION_HARD_FAILURE,
                            activeLastFlushedSequence.get(),
                            telemetry.snapshot().droppedFrames,
                            failureDetail(error));
                }
                String errorCode = errorCodeFor(error);
                // Once a framed session exists, its exact hard-failure terminal
                // above is authoritative. This path is only for pre-channel
                // failures such as digital-zero startup PCM.
                if (activeStreamSessionId.get() <= 0L) {
                    publishStartupFailureToPhone(
                            phoneNodeId, requestId, errorCode, failureDetail(error));
                }
                publishTelemetry(telemetry.fail(errorCode, error), true);
                showFailureNotification(failureDetail(error));
                Log.e(TAG, "Watch microphone uplink failed request=" + requestId
                        + " phoneNode=" + phoneNodeId, error);
            }
        } finally {
            finishWorkerSession(generation, resources, failed);
        }
    }

    /**
     * A startup failure has no framed PCM channel and therefore cannot use the
     * normal terminal message. Publish the exact command failure separately so
     * the phone can finish the same durable PREPARE instead of waiting 30 s.
     */
    private void publishStartupFailureToPhone(
            String phoneNodeId, String requestId, String code, String detail) {
        if (!WatchStartupFailureOutbox.publish(
                getApplicationContext(), phoneNodeId, requestId, code, detail)) {
            Log.e(TAG, "Could not persist startup failure request=" + requestId);
        }
    }

    /**
     * Acquires and proves the Watch6 built-in microphone before the phone is
     * allowed to see a framed PCM channel. The preferred-device request is not
     * treated as proof: the routed AudioRecord endpoint must also report
     * TYPE_BUILTIN_MIC. A zero-only recorder is fully reopened once, but no
     * alternate audio source or external device is ever selected.
     */
    private BuiltInCaptureSetup openBuiltInCaptureWithStartupProof(long generation)
            throws Exception {
        ZeroPcmStartupGuard startupGuard = new ZeroPcmStartupGuard();
        while (true) {
            SessionResources resources = null;
            try {
                AudioManager manager = getSystemService(AudioManager.class);
                if (manager == null) {
                    throw new IllegalStateException("AudioManager unavailable");
                }
                resources = new SessionResources(
                        getApplicationContext(),
                        manager,
                        manager.getMode(),
                        manager.getCommunicationDevice());
                installResources(generation, resources);

                // Remove any stale communication route before creating AudioRecord.
                // The old mode/device are restored by SessionResources.close().
                manager.clearCommunicationDevice();
                manager.setMode(AudioManager.MODE_NORMAL);
                AudioDeviceInfo builtInInput = awaitBuiltInInput(manager, generation);
                int minBuffer = AudioRecord.getMinBufferSize(
                        WearAudioProtocol.SAMPLE_RATE,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT);
                if (minBuffer <= 0) {
                    throw new IllegalStateException(
                            "Нет 16 kHz mono буфера для встроенного микрофона Watch6");
                }
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                        != PackageManager.PERMISSION_GRANTED) {
                    throw new SecurityException(
                            "RECORD_AUDIO was revoked before AudioRecord creation");
                }
                AudioRecord record = new AudioRecord.Builder()
                        .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                        .setAudioFormat(new AudioFormat.Builder()
                                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                                .setSampleRate(WearAudioProtocol.SAMPLE_RATE)
                                .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                                .build())
                        .setBufferSizeInBytes(Math.max(
                                minBuffer * 2, WearAudioProtocol.SAMPLE_RATE * 2))
                        .build();
                if (record.getState() != AudioRecord.STATE_INITIALIZED) {
                    record.release();
                    throw new IllegalStateException(
                            "AudioRecord встроенного микрофона Watch6 не инициализировался");
                }
                if (record.getSampleRate() != WearAudioProtocol.SAMPLE_RATE
                        || record.getChannelCount() != 1
                        || record.getAudioFormat() != AudioFormat.ENCODING_PCM_16BIT) {
                    record.release();
                    throw new IllegalStateException(
                            "Watch6 не подтвердил PCM16 mono 16 kHz: rate="
                                    + record.getSampleRate()
                                    + " channels=" + record.getChannelCount()
                                    + " format=" + record.getAudioFormat());
                }
                if (!record.setPreferredDevice(builtInInput)) {
                    record.release();
                    throw new IllegalStateException(
                            "Watch6 отверг встроенный микрофон как preferred input");
                }
                resources.setRecord(record);

                record.startRecording();
                if (record.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                    throw new IllegalStateException("Встроенный микрофон Watch6 не запустился");
                }
                resources.markCaptureStarted();
                if (gracefulStopRequested.get()) resources.requestProducerStop();
                AudioDeviceInfo routedInput = awaitRecordRoute(
                        record, builtInInput, generation);
                if (!isSameBuiltInEndpoint(builtInInput, routedInput)) {
                    throw new IllegalStateException(
                            "Маршрут встроенного микрофона Watch6 не подтверждён");
                }

                StartupPcmFrame startup = awaitFirstNonzeroBuiltInFrame(
                        record,
                        resources,
                        builtInInput,
                        generation,
                        startupGuard);
                return new BuiltInCaptureSetup(
                        resources,
                        record,
                        builtInInput,
                        startup,
                        startupGuard.reacquisitions());
            } catch (ZeroPcmReacquireRequired retry) {
                retireResources(resources);
                Log.w(TAG, "Watch6 built-in startup PCM stayed all-zero; "
                        + "fully reopening the same built-in route once");
                updateNotification(
                        "Микрофон Watch6 передал только нули · открываю его ещё раз…");
                requireGeneration(generation);
                Thread.sleep(200L);
            } catch (Exception failure) {
                retireResources(resources);
                throw failure;
            }
        }
    }

    private StartupPcmFrame awaitFirstNonzeroBuiltInFrame(
            AudioRecord record,
            SessionResources resources,
            AudioDeviceInfo expectedInput,
            long generation,
            ZeroPcmStartupGuard startupGuard) throws Exception {
        short[] samples = new short[WearAudioProtocol.FRAME_SAMPLES];
        long samplesRead = 0L;
        while (true) {
            requireGeneration(generation);
            int filled = 0;
            while (filled < samples.length) {
                requireGeneration(generation);
                int read = record.read(
                        samples,
                        filled,
                        samples.length - filled,
                        AudioRecord.READ_BLOCKING);
                if (read == AudioRecord.ERROR_DEAD_OBJECT) {
                    throw new IllegalStateException(
                            "Встроенный микрофон Watch6 отключился при проверке старта");
                }
                if (read < 0) {
                    throw new IllegalStateException(
                            "Ошибка AudioRecord Watch6 при старте: " + read);
                }
                if (read == 0) continue;
                filled += read;
                samplesRead += read;
            }

            AudioDeviceInfo routedInput = record.getRoutedDevice();
            if (!isSameBuiltInEndpoint(expectedInput, routedInput)) {
                throw new IllegalStateException(
                        "AudioRecord ушёл со встроенного микрофона Watch6 при старте");
            }

            ZeroPcmStartupGuard.Decision decision = startupGuard.observe(samples);
            if (decision == ZeroPcmStartupGuard.Decision.PROVEN) {
                return new StartupPcmFrame(
                        samplesRead,
                        SystemClock.elapsedRealtimeNanos(),
                        samples.clone());
            }
            if (decision == ZeroPcmStartupGuard.Decision.REACQUIRE) {
                throw new ZeroPcmReacquireRequired();
            }
            if (decision == ZeroPcmStartupGuard.Decision.FAIL) {
                boolean systemMute = false;
                try { systemMute = resources.manager.isMicrophoneMute(); }
                catch (RuntimeException ignored) {}
                throw new WatchBuiltInZeroPcmException(
                        "Встроенный микрофон Watch6 дважды передал только цифровые нули"
                                + (systemMute ? " · системный mute часов включён" : ""));
            }
        }
    }

    private void retireResources(SessionResources resources) {
        if (resources == null) return;
        synchronized (this) {
            if (activeResources == resources) activeResources = null;
        }
        resources.close();
    }

    private synchronized void installResources(long generation, SessionResources resources)
            throws InterruptedException {
        if (!isGenerationActive(generation)) {
            resources.close();
            throw new InterruptedException("Watch microphone generation cancelled before install");
        }
        SessionResources previous = activeResources;
        activeResources = resources;
        if (previous != null && previous != resources) previous.close();
    }

    private void finishWorkerSession(
            long generation, SessionResources resources, boolean failed) {
        synchronized (this) {
            if (activeResources == resources) activeResources = null;
        }
        if (resources != null) resources.close();
        if (streamGeneration.compareAndSet(generation, generation + 1L)) {
            running.set(false);
            CAPTURE_SERVICE_ACTIVE.set(false);
            gracefulStopRequested.set(false);
            hardAbortRequested.set(false);
            activePhoneNodeId = "";
            activeRequestId = "";
            activeStreamSessionId.set(0L);
            activeLastFlushedSequence.set(-1L);
            activePhoneBinding.set(null);
            activeDrainOwner.set(null);
            activeDrainAcceptedAtElapsedMs = 0L;
            WearAudioTerminalAckRegistry.clear();
            releaseWakeLock();
            if (!failed) publishTelemetry(
                    telemetry.advance(WearMicTelemetry.Stage.STOPPED), false);
            ControllerStateStore.updateWatchMicrophone(
                    this,
                    false,
                    failed
                            ? telemetryStatus(telemetry.snapshot())
                            : "Микрофон Watch6 остановлен");
            ComplicationRefresh.request(this, ControllerStateStore.Kind.LIVE);
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
        }
        if (worker == Thread.currentThread()) worker = null;
    }

    private StreamResult streamFrames(
            AudioRecord record,
            ChannelWriter initialWriter,
            SessionResources resources,
            AudioDeviceInfo expectedInput,
            String phoneNodeId,
            long generation,
            StartupPcmFrame startup) throws Exception {
        ArrayBlockingQueue<AudioFrame> queue = new ArrayBlockingQueue<>(MAX_QUEUED_FRAMES);
        AtomicBoolean captureRunning = new AtomicBoolean(true);
        AtomicReference<Throwable> captureFailure = new AtomicReference<>();
        Thread capture = new Thread(
                () -> captureFrames(
                        record,
                        expectedInput,
                        queue,
                        captureRunning,
                        captureFailure,
                        generation,
                        startup),
                "watch-mic-capture");
        capture.start();

        ChannelWriter writer = initialWriter;
        long lastFlushedSequence = -1L;
        boolean forceGap = false;
        int reconnectAttemptIndex = 0;
        long reconnectDeadlineMs = 0L;
        long drainDeadlineMs = 0L;
        try {
            while (true) {
                boolean owned = isGenerationOwned(generation);
                boolean graceful = gracefulStopRequested.get();
                if (graceful && drainDeadlineMs == 0L) {
                    drainDeadlineMs = safeElapsedDeadline(
                            SystemClock.elapsedRealtime(), requestedDrainTimeoutMs);
                    resources.requestProducerStop();
                }
                boolean queueEmpty = queue.isEmpty();
                if (GracefulDrainPolicy.drainExpired(
                        graceful,
                        drainDeadlineMs,
                        SystemClock.elapsedRealtime(),
                        captureRunning.get(),
                        queueEmpty)) {
                    throw new IllegalStateException("graceful PCM drain deadline expired");
                }
                if (!GracefulDrainPolicy.shouldWriterContinue(
                        owned,
                        hardAbortRequested.get(),
                        captureRunning.get(),
                        queueEmpty)) break;

                Throwable failure = captureFailure.get();
                if (failure != null) {
                    throw new IllegalStateException("Watch microphone capture failed", failure);
                }
                AudioFrame frame = queue.poll(WRITER_POLL_MS, TimeUnit.MILLISECONDS);
                if (frame == null) continue;

                int frameFlags = FrameContinuityPolicy.flagsFor(
                        frame.flags,
                        lastFlushedSequence,
                        frame.sequence,
                        forceGap);
                try {
                    WearAudioProtocol.writeFrame(
                            writer.output,
                            frame.sequence,
                            frame.captureElapsedNanos,
                            frameFlags,
                            frame.pcmLittleEndian);
                    // Per-frame flush makes lastSequence an exact accepted-wire
                    // boundary for the phone's archive FINISH gate.
                    writer.output.flush();
                    lastFlushedSequence = Integer.toUnsignedLong(frame.sequence);
                    activeLastFlushedSequence.set(lastFlushedSequence);
                    maybePublishFrameTelemetry(telemetry.sent(), false);
                    forceGap = false;
                } catch (Exception transportFailure) {
                    maybePublishFrameTelemetry(telemetry.dropped(), true);
                    forceGap = true;
                    resources.closeTransport();
                    if (!isGenerationOwned(generation)) throw transportFailure;
                    if (reconnectDeadlineMs == 0L) {
                        reconnectDeadlineMs = ChannelReconnectPolicy.deadline(
                                SystemClock.elapsedRealtime());
                    }
                    ReconnectResult replacement = openReplacementChannel(
                            resources,
                            phoneNodeId,
                            generation,
                            reconnectAttemptIndex,
                            reconnectDeadlineMs,
                            transportFailure);
                    writer = replacement.writer;
                    reconnectAttemptIndex = replacement.nextAttemptIndex;
                }
            }
            writer.output.flush();
            Throwable failure = captureFailure.get();
            if (failure != null && isGenerationOwned(generation)) {
                throw new IllegalStateException("Watch microphone capture failed", failure);
            }
            return new StreamResult(
                    writer.sessionId,
                    lastFlushedSequence,
                    telemetry.snapshot().droppedFrames);
        } finally {
            captureRunning.set(false);
            resources.requestProducerStop();
            if (!gracefulStopRequested.get()) capture.interrupt();
            capture.join(1_000L);
            if (capture.isAlive()) {
                capture.interrupt();
                capture.join(250L);
            }
        }
    }

    private void captureFrames(
            AudioRecord record,
            AudioDeviceInfo expectedInput,
            ArrayBlockingQueue<AudioFrame> queue,
            AtomicBoolean captureRunning,
            AtomicReference<Throwable> captureFailure,
            long generation,
            StartupPcmFrame startup) {
        short[] samples = new short[WearAudioProtocol.FRAME_SAMPLES];
        int sequence = 0;
        long samplesRead = startup.frameEndPosition;
        AudioTimestamp hardwareTimestamp = new AudioTimestamp();
        CaptureTimestampEstimator captureClock =
                new CaptureTimestampEstimator(WearAudioProtocol.SAMPLE_RATE);
        StartupFrameBuffer<PendingAudioFrame> timestampStartup =
                new StartupFrameBuffer<>(TIMESTAMP_STARTUP_FRAMES);
        timestampStartup.addLast(new PendingAudioFrame(
                startup.frameEndPosition,
                startup.readDoneBootNanos,
                startup.pcmLittleEndian));
        boolean captureClockStarted = false;
        try {
            captureLoop:
            while (GracefulDrainPolicy.shouldCapture(
                    isGenerationOwned(generation),
                    gracefulStopRequested.get(),
                    hardAbortRequested.get())
                    && captureRunning.get()
                    && !Thread.currentThread().isInterrupted()) {
                int filled = 0;
                while (filled < samples.length && GracefulDrainPolicy.shouldCapture(
                        isGenerationOwned(generation),
                        gracefulStopRequested.get(),
                        hardAbortRequested.get())) {
                    int read = record.read(
                            samples,
                            filled,
                            samples.length - filled,
                            AudioRecord.READ_BLOCKING);
                    if (read == AudioRecord.ERROR_DEAD_OBJECT) {
                        if (gracefulStopRequested.get()) break captureLoop;
                        throw new IllegalStateException("Встроенный микрофон Watch6 отключился");
                    }
                    if (read < 0) {
                        if (gracefulStopRequested.get()) break captureLoop;
                        throw new IllegalStateException("Ошибка AudioRecord Watch6: " + read);
                    }
                    if (read == 0) continue;
                    filled += read;
                    samplesRead += read;
                }
                if (filled != samples.length) break;

                AudioDeviceInfo routedInput = record.getRoutedDevice();
                if (!isSameBuiltInEndpoint(expectedInput, routedInput)) {
                    throw new IllegalStateException(
                            "AudioRecord ушёл со встроенного микрофона Watch6");
                }

                WearMicTelemetry.FrameObservation observation = telemetry.captured(samples);
                maybePublishFrameTelemetry(observation.snapshot, observation.firstNonzero);
                long readDoneBootNanos = SystemClock.elapsedRealtimeNanos();
                boolean hardwareTimestampValid = false;
                try {
                    hardwareTimestampValid = record.getTimestamp(
                            hardwareTimestamp,
                            AudioTimestamp.TIMEBASE_BOOTTIME) == AudioRecord.SUCCESS;
                } catch (Exception ignored) {
                    // Stateful fallback preserves monotonic wire timestamps.
                }
                byte[] pcm = new byte[WearAudioProtocol.PCM_BYTES_PER_FRAME];
                for (int index = 0; index < samples.length; index++) {
                    int value = samples[index];
                    pcm[index * 2] = (byte) (value & 0xff);
                    pcm[index * 2 + 1] = (byte) ((value >>> 8) & 0xff);
                }

                if (!captureClockStarted) {
                    timestampStartup.addLast(new PendingAudioFrame(
                            samplesRead, readDoneBootNanos, pcm));
                    boolean hardwareReady = hardwareTimestampValid
                            && captureClock.isInitialHardwareAnchorUsable(
                            samplesRead,
                            hardwareTimestamp.framePosition,
                            hardwareTimestamp.nanoTime,
                            readDoneBootNanos);
                    boolean fallbackReady = timestampStartup.size() >= TIMESTAMP_STARTUP_FRAMES;
                    if (!hardwareReady && !fallbackReady) continue;
                    if (!hardwareReady) captureClock.lockToFallback();
                    while (!timestampStartup.isEmpty()) {
                        PendingAudioFrame pending = timestampStartup.removeFirst();
                        long captureEndNanos = captureClock.estimate(
                                pending.frameEndPosition,
                                hardwareReady,
                                hardwareTimestamp.framePosition,
                                hardwareTimestamp.nanoTime,
                                pending.readDoneBootNanos);
                        sequence = enqueueCapturedFrame(
                                queue,
                                generation,
                                sequence,
                                captureEndNanos,
                                pending.pcmLittleEndian);
                    }
                    captureClockStarted = true;
                    continue;
                }

                long captureEndNanos = captureClock.estimate(
                        samplesRead,
                        hardwareTimestampValid,
                        hardwareTimestamp.framePosition,
                        hardwareTimestamp.nanoTime,
                        readDoneBootNanos);
                sequence = enqueueCapturedFrame(
                        queue,
                        generation,
                        sequence,
                        captureEndNanos,
                        pcm);
            }
        } catch (Throwable failure) {
            if (isGenerationOwned(generation)
                    && captureRunning.get()
                    && !gracefulStopRequested.get()) {
                captureFailure.set(failure);
            }
        } finally {
            // A short session can stop before the fifth frame or the first usable
            // hardware anchor. Every complete startup frame still belongs to the
            // raw stream, so lock one fallback clock and enqueue it before marking
            // the producer complete.
            if (gracefulStopRequested.get()
                    && isGenerationOwned(generation)
                    && captureFailure.get() == null
                    && !timestampStartup.isEmpty()) {
                try {
                    if (!captureClockStarted) captureClock.lockToFallback();
                    while (!timestampStartup.isEmpty()) {
                        PendingAudioFrame pending = timestampStartup.removeFirst();
                        long captureEndNanos = captureClock.estimate(
                                pending.frameEndPosition,
                                false,
                                0L,
                                0L,
                                pending.readDoneBootNanos);
                        sequence = enqueueCapturedFrame(
                                queue,
                                generation,
                                sequence,
                                captureEndNanos,
                                pending.pcmLittleEndian);
                    }
                } catch (Throwable flushFailure) {
                    captureFailure.compareAndSet(null, flushFailure);
                }
            }
            captureRunning.set(false);
        }
    }

    private int enqueueCapturedFrame(
            ArrayBlockingQueue<AudioFrame> queue,
            long generation,
            int sequence,
            long captureEndNanos,
            byte[] pcmLittleEndian) {
        AudioFrame frame = new AudioFrame(sequence, captureEndNanos, 0, pcmLittleEndian);
        if (!queue.offer(frame)) {
            boolean accepted = false;
            while (isGenerationOwned(generation) && !accepted) {
                if (gracefulStopRequested.get()) {
                    // Once STOP owns the session, never evict any of the twelve
                    // already captured tail frames. The writer is still alive
                    // and will free a slot; bounded drain timeout remains the
                    // fail-safe if it cannot.
                    try {
                        accepted = queue.offer(
                                frame, WRITER_POLL_MS, TimeUnit.MILLISECONDS);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                    continue;
                }
                if (queue.poll() != null) {
                    maybePublishFrameTelemetry(telemetry.dropped(), true);
                }
                accepted = queue.offer(frame);
            }
            if (!accepted && isGenerationOwned(generation)) {
                maybePublishFrameTelemetry(telemetry.dropped(), true);
            }
        }
        return sequence + 1;
    }

    private ChannelWriter openFramedChannel(
            SessionResources resources,
            String phoneNodeId,
            long generation,
            long timeoutMs,
            boolean allowGraceful) throws Exception {
        if (!isGenerationOwned(generation)
                || (!allowGraceful && gracefulStopRequested.get())) {
            throw new InterruptedException("Watch microphone channel generation cancelled");
        }
        long openDeadlineMs = safeElapsedDeadline(
                SystemClock.elapsedRealtime(), Math.max(1L, timeoutMs));
        ChannelClient client = Wearable.getChannelClient(getApplicationContext());
        AsyncResourceOwner<ChannelClient.Channel> channelOwnership =
                new AsyncResourceOwner<>(channel -> closeLooseChannel(client, channel));
        Task<ChannelClient.Channel> channelTask =
                client.openChannel(phoneNodeId, WearAudioProtocol.CHANNEL_PATH);
        channelTask.addOnSuccessListener(channelOwnership::observe);

        ChannelClient.Channel channel;
        try {
            long channelTimeoutMs = remainingElapsedTimeout(openDeadlineMs);
            if (channelTimeoutMs <= 0L) {
                throw new java.util.concurrent.TimeoutException("Watch channel deadline expired");
            }
            channel = Tasks.await(channelTask, channelTimeoutMs, TimeUnit.MILLISECONDS);
            if (channel == null
                    || !isGenerationOwned(generation)
                    || (!allowGraceful && gracefulStopRequested.get())
                    || !channelOwnership.claim(channel)) {
                channelOwnership.abandon();
                throw new InterruptedException(
                        "Watch microphone channel became obsolete while opening");
            }
        } catch (Exception error) {
            channelOwnership.abandon();
            throw error;
        }

        AsyncResourceOwner<OutputStream> outputOwnership =
                new AsyncResourceOwner<>(WearMicForegroundService::closeLooseOutput);
        Task<OutputStream> outputTask = client.getOutputStream(channel);
        outputTask.addOnSuccessListener(outputOwnership::observe);

        OutputStream raw;
        try {
            long outputTimeoutMs = remainingElapsedTimeout(openDeadlineMs);
            if (outputTimeoutMs <= 0L) {
                throw new java.util.concurrent.TimeoutException("Watch output deadline expired");
            }
            raw = Tasks.await(outputTask, outputTimeoutMs, TimeUnit.MILLISECONDS);
            if (raw == null
                    || !isGenerationOwned(generation)
                    || (!allowGraceful && gracefulStopRequested.get())
                    || !outputOwnership.claim(raw)) {
                outputOwnership.abandon();
                closeLooseChannel(client, channel);
                throw new InterruptedException(
                        "Watch microphone output became obsolete while opening");
            }
            resources.installTransport(channel, raw);
        } catch (Exception error) {
            outputOwnership.abandon();
            closeLooseChannel(client, channel);
            throw error;
        }

        long sessionId = nextSessionId();
        DataOutputStream output = new DataOutputStream(new BufferedOutputStream(
                raw, WearAudioProtocol.PCM_BYTES_PER_FRAME * 2));
        try {
            WearAudioProtocol.writeHeader(output, sessionId);
            output.flush();
            if (!isGenerationOwned(generation)) {
                throw new InterruptedException(
                        "Watch microphone header belongs to obsolete generation");
            }
            activeStreamSessionId.set(sessionId);
            return new ChannelWriter(sessionId, output);
        } catch (Exception error) {
            resources.closeTransport();
            throw error;
        }
    }

    private ReconnectResult openReplacementChannel(
            SessionResources resources,
            String phoneNodeId,
            long generation,
            int firstAttemptIndex,
            long reconnectDeadlineMs,
            Throwable firstFailure) throws Exception {
        Throwable lastFailure = firstFailure;
        int attemptIndex = firstAttemptIndex;
        while (ChannelReconnectPolicy.mayAttempt(
                attemptIndex, SystemClock.elapsedRealtime(), reconnectDeadlineMs)) {
            if (!isGenerationOwned(generation)) {
                throw new InterruptedException(
                        "Watch microphone replacement generation cancelled");
            }
            long delay = ChannelReconnectPolicy.backoffMs(attemptIndex);
            long remaining = reconnectDeadlineMs - SystemClock.elapsedRealtime();
            if (delay < 0L || remaining <= 0L) break;
            Thread.sleep(Math.min(delay, remaining));
            long timeout = ChannelReconnectPolicy.taskTimeoutMs(
                    SystemClock.elapsedRealtime(), reconnectDeadlineMs);
            if (timeout <= 0L) break;
            int attempted = attemptIndex;
            attemptIndex++;
            try {
                ChannelWriter replacement = openFramedChannel(
                        resources,
                        phoneNodeId,
                        generation,
                        timeout,
                        true);
                Log.w(TAG, "Watch PCM replacement channel opened attempt="
                        + (attempted + 1) + " session=" + replacement.sessionId);
                return new ReconnectResult(replacement, attemptIndex);
            } catch (InterruptedException interrupted) {
                throw interrupted;
            } catch (Exception retryFailure) {
                lastFailure = retryFailure;
                resources.closeTransport();
                Log.w(TAG, "Watch PCM replacement failed attempt="
                        + (attempted + 1), retryFailure);
            }
        }
        throw new IllegalStateException(
                "Watch PCM replacement budget exhausted after "
                        + attemptIndex + " attempts",
                lastFailure);
    }

    private boolean publishTerminal(
            long generation,
            String disposition,
            long lastSequence,
            long droppedFrames,
            String detail) {
        long sessionId = activeStreamSessionId.get();
        DrainRequestOwner drainOwner = activeDrainOwner.get();
        long bindingWaitMs = CrossDeviceDrainBudgetPolicy.bindingWaitBudgetMs(
                PHONE_BINDING_WAIT_MS,
                drainOwner == null ? 0L : activeDrainAcceptedAtElapsedMs,
                SystemClock.elapsedRealtime());
        PhoneStreamBinding binding = awaitCurrentPhoneBinding(
                generation, sessionId, bindingWaitMs);
        if (binding == null || sessionId <= 0L) {
            Log.w(TAG, "Cannot publish exact terminal: current phone binding is absent");
            return false;
        }
        String terminalRequestId = drainOwner == null
                ? binding.requestId : drainOwner.requestId;
        String payload = WearAudioLifecycleProtocol.terminalJson(
                terminalRequestId,
                binding.captureGroupId,
                binding.runGeneration,
                sessionId,
                disposition,
                lastSequence,
                droppedFrames,
                detail);
        if (payload.isEmpty()) {
            Log.e(TAG, "Refusing invalid terminal payload");
            return false;
        }
        WearAudioTerminalAckRegistry.clear();
        if (!WearAudioTerminalAckRegistry.expect(disposition, lastSequence)) {
            Log.e(TAG, "Refusing invalid terminal ACK expectation");
            return false;
        }
        byte[] immutablePayload = payload.getBytes(StandardCharsets.UTF_8);
        long ackBudgetMs = CrossDeviceDrainBudgetPolicy.ackBudgetMs(
                drainOwner == null ? 0L : activeDrainAcceptedAtElapsedMs,
                SystemClock.elapsedRealtime());
        try {
            WearAudioTerminalAckRegistry.Ack ack =
                    TerminalRetryPolicy.sendUntilAck(
                            ackBudgetMs,
                            new TerminalRetryPolicy.Clock() {
                                @Override public long nowMs() {
                                    return SystemClock.elapsedRealtime();
                                }

                                @Override public void sleepMs(long delayMs)
                                        throws InterruptedException {
                                    Thread.sleep(delayMs);
                                }
                            },
                            attempt -> {
                                if (!isGenerationOwned(generation)) return;
                                // MessageClient task success proves only local
                                // queueing. Every retry carries the exact same
                                // immutable terminal bytes.
                                try {
                                    Wearable.getMessageClient(getApplicationContext()).sendMessage(
                                            binding.phoneNodeId,
                                            WearAudioLifecycleProtocol.PATH_TERMINAL,
                                            immutablePayload);
                                } catch (RuntimeException sendFailure) {
                                    Log.w(TAG, "Watch PCM terminal enqueue failed attempt="
                                            + (attempt + 1), sendFailure);
                                }
                                Log.i(TAG, "Published Watch PCM terminal attempt="
                                        + (attempt + 1)
                                        + " disposition=" + disposition
                                        + " session=" + sessionId
                                        + " last=" + lastSequence
                                        + " dropped=" + droppedFrames);
                            },
                            WearAudioTerminalAckRegistry::await);
            return ack != null && ack.confirmsGraceful(lastSequence);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        } catch (Exception error) {
            Log.w(TAG, "Watch PCM terminal send/ACK failed", error);
            return false;
        }
    }

    private PhoneStreamBinding awaitCurrentPhoneBinding(
            long generation, long sessionId, long timeoutMs) {
        long deadline = safeElapsedDeadline(SystemClock.elapsedRealtime(), timeoutMs);
        while (isGenerationOwned(generation) && SystemClock.elapsedRealtime() <= deadline) {
            PhoneStreamBinding binding = activePhoneBinding.get();
            if (binding != null && binding.sessionId == sessionId) return binding;
            try {
                Thread.sleep(25L);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return null;
            }
        }
        return null;
    }

    private long nextSessionId() {
        long previous = activeStreamSessionId.get();
        long candidate = Math.max(1L, SystemClock.elapsedRealtimeNanos());
        if (candidate == previous) candidate = candidate == Long.MAX_VALUE ? 1L : candidate + 1L;
        return candidate;
    }

    private static long safeElapsedDeadline(long nowElapsedMs, long timeoutMs) {
        return nowElapsedMs > Long.MAX_VALUE - timeoutMs
                ? Long.MAX_VALUE : nowElapsedMs + timeoutMs;
    }

    private static long remainingElapsedTimeout(long deadlineElapsedMs) {
        return Math.max(0L, deadlineElapsedMs - SystemClock.elapsedRealtime());
    }

    private static void closeLooseOutput(OutputStream output) {
        if (output == null) return;
        try { output.close(); } catch (Exception ignored) {}
    }

    private static void closeLooseChannel(
            ChannelClient client, ChannelClient.Channel channel) {
        if (client == null || channel == null) return;
        try { client.close(channel); } catch (Exception ignored) {}
    }

    private Node awaitExactPhoneNode(String exactNodeId, long generation) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + CHANNEL_TIMEOUT_MS;
        while (SystemClock.elapsedRealtime() < deadline) {
            requireGeneration(generation);
            List<Node> nodes = Tasks.await(
                    Wearable.getNodeClient(getApplicationContext()).getConnectedNodes(),
                    5,
                    TimeUnit.SECONDS);
            Node exact = null;
            for (Node node : nodes) {
                if (node == null || !exactNodeId.equals(node.getId())) continue;
                if (exact != null) {
                    throw new IllegalStateException("Duplicate exact phone node");
                }
                exact = node;
            }
            if (exact != null) return exact;
            Thread.sleep(100L);
        }
        throw new IllegalStateException("Exact accepted phone node is not connected");
    }

    private AudioDeviceInfo awaitBuiltInInput(
            AudioManager manager, long generation) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + ROUTE_TIMEOUT_MS;
        while (SystemClock.elapsedRealtime() < deadline) {
            requireGeneration(generation);
            AudioDeviceInfo selected = null;
            for (AudioDeviceInfo device
                    : manager.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
                if (device == null
                        || device.getType() != AudioDeviceInfo.TYPE_BUILTIN_MIC
                        || !device.isSource()) continue;
                if (selected == null || device.getId() < selected.getId()) {
                    selected = device;
                }
            }
            if (selected != null) return selected;
            Thread.sleep(50L);
        }
        throw new IllegalStateException(
                "Watch6 не опубликовал TYPE_BUILTIN_MIC");
    }

    private AudioDeviceInfo awaitRecordRoute(
            AudioRecord record, AudioDeviceInfo expected, long generation) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + ROUTE_TIMEOUT_MS;
        AudioDeviceInfo last = null;
        while (SystemClock.elapsedRealtime() < deadline) {
            requireGeneration(generation);
            last = record.getRoutedDevice();
            if (isSameBuiltInEndpoint(expected, last)) return last;
            Thread.sleep(50L);
        }
        return last;
    }

    private void requireGeneration(long generation) throws InterruptedException {
        if (!isGenerationActive(generation)) {
            throw new InterruptedException("Watch microphone generation cancelled");
        }
    }

    private static boolean sameEndpoint(AudioDeviceInfo expected, AudioDeviceInfo actual) {
        if (expected == null || actual == null || expected.getType() != actual.getType()) {
            return false;
        }
        if (expected.getId() == actual.getId()) return true;
        String expectedAddress = clean(expected.getAddress());
        return !expectedAddress.isEmpty()
                && expectedAddress.equalsIgnoreCase(clean(actual.getAddress()));
    }

    private static boolean isSameBuiltInEndpoint(
            AudioDeviceInfo expected, AudioDeviceInfo actual) {
        return expected != null
                && actual != null
                && expected.getType() == AudioDeviceInfo.TYPE_BUILTIN_MIC
                && actual.getType() == AudioDeviceInfo.TYPE_BUILTIN_MIC
                && (expected.getId() == actual.getId()
                || sameEndpoint(expected, actual)
                || clean(expected.getAddress()).isEmpty()
                || clean(actual.getAddress()).isEmpty());
    }

    private void createNotificationChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Микрофон Watch6 переводчика",
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Передача встроенного микрофона Watch6 в Шушуню");
        manager.createNotificationChannel(channel);
        NotificationChannel failure = new NotificationChannel(
                FAILURE_CHANNEL_ID,
                "Микрофон Watch6 не запущен",
                NotificationManager.IMPORTANCE_DEFAULT);
        failure.setDescription("Причина отказа встроенного микрофона Watch6");
        manager.createNotificationChannel(failure);
    }

    private void showFailureNotification(String detail) {
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) return;
        String message = "Встроенный микрофон Watch6 не запущен. "
                + cleanFailureDetail(detail);
        manager.notify(
                FAILURE_NOTIFICATION_ID,
                new Notification.Builder(this, FAILURE_CHANNEL_ID)
                        .setSmallIcon(R.drawable.ic_shushunya)
                        .setContentTitle("Шушуня · микрофон Watch6 не запущен")
                        .setContentText(message)
                        .setStyle(new Notification.BigTextStyle().bigText(message))
                        .setOnlyAlertOnce(true)
                        .setAutoCancel(true)
                        .build());
    }

    private void cancelFailureNotification() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.cancel(FAILURE_NOTIFICATION_ID);
    }

    private static String failureDetail(Throwable error) {
        if (error == null) return "";
        String message = clean(error.getMessage());
        return message.isEmpty() ? error.getClass().getSimpleName() : message;
    }

    private static String cleanFailureDetail(String detail) {
        String clean = clean(detail).replace('\n', ' ').replace('\r', ' ');
        return clean.length() <= 240 ? clean : clean.substring(0, 240);
    }

    private void startConnectedForeground(String status) {
        startForeground(
                NOTIFICATION_ID,
                buildNotification(status),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
    }

    private void startCaptureForeground(String status) {
        startForeground(
                NOTIFICATION_ID,
                buildNotification(status),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
                        | ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
    }

    private void updateNotification(String status) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.notify(NOTIFICATION_ID, buildNotification(status));
    }

    private void maybePublishFrameTelemetry(
            WearMicTelemetry.Snapshot snapshot, boolean milestone) {
        if (snapshot == null) return;
        boolean publish = milestone;
        synchronized (this) {
            if (snapshot.sentFrames - lastTelemetryNotificationFrame
                    >= TELEMETRY_NOTIFICATION_INTERVAL_FRAMES) {
                lastTelemetryNotificationFrame = snapshot.sentFrames;
                publish = true;
            }
        }
        if (publish) publishTelemetry(snapshot, true);
    }

    private void publishTelemetry(WearMicTelemetry.Snapshot snapshot, boolean notify) {
        if (snapshot == null) return;
        String status = telemetryStatus(snapshot);
        if (snapshot.stage == WearMicTelemetry.Stage.ERROR) Log.e(TAG, status);
        else Log.i(TAG, status);
        boolean active = snapshot.stage == WearMicTelemetry.Stage.FIRST_NONZERO_FRAME
                && snapshot.firstNonzeroFrameSeen;
        if (ControllerStateStore.updateWatchMicrophone(this, active, status)) {
            ComplicationRefresh.request(this, ControllerStateStore.Kind.LIVE);
        }
        if (active) WatchMicStatusNotification.cancel(this);
        if (notify) updateNotification(status);
    }

    private String errorCodeForCurrentStage() {
        switch (telemetry.snapshot().stage) {
            case PERMISSION_GRANTED:
            case FOREGROUND_SERVICE:
                return "WATCH_BUILTIN_MIC";
            case NEARBY_NODE:
                return "CHANNEL_OPEN";
            case CHANNEL_OPEN:
                return "AUDIO_RECORD";
            case AUDIO_ROUTE:
            case FIRST_NONZERO_FRAME:
                return "STREAM_IO";
            default:
                return "WATCH_MIC_UPLINK";
        }
    }

    private String errorCodeFor(Throwable error) {
        if (error instanceof WatchBuiltInZeroPcmException) {
            return "WATCH_BUILTIN_ZERO_PCM";
        }
        return errorCodeForCurrentStage();
    }

    private static String telemetryStatus(WearMicTelemetry.Snapshot snapshot) {
        String stage;
        switch (snapshot.stage) {
            case PERMISSION_GRANTED:
                stage = "Разрешение микрофона Watch6 получено";
                break;
            case FOREGROUND_SERVICE:
                stage = "Микрофон Watch6 запущен в фоне";
                break;
            case NEARBY_NODE:
                stage = "Exact телефон найден";
                break;
            case CHANNEL_OPEN:
                stage = "PCM-канал на телефон открыт";
                break;
            case AUDIO_ROUTE:
                stage = "Встроенный микрофон Watch6 выбран";
                break;
            case FIRST_NONZERO_FRAME:
                stage = "PCM микрофона Watch6 передаётся";
                break;
            case ERROR:
                stage = "Ошибка " + snapshot.lastError;
                break;
            case STOPPED:
                stage = "Микрофон Watch6 остановлен";
                break;
            case IDLE:
            default:
                stage = "Микрофон Watch6 ожидает запуска";
                break;
        }
        String source = snapshot.audioSource.isEmpty() ? "" : " · " + snapshot.audioSource;
        String zeroHint = snapshot.capturedFrames >= TELEMETRY_NOTIFICATION_INTERVAL_FRAMES
                && !snapshot.firstNonzeroFrameSeen ? " · PCM пока нулевой" : "";
        return String.format(
                Locale.US,
                "%s%s · tx %d/%d · RMS %.0f · drop %d%s",
                stage,
                source,
                snapshot.sentFrames,
                snapshot.capturedFrames,
                snapshot.lastRms,
                snapshot.droppedFrames,
                zeroHint);
    }

    private Notification buildNotification(String status) {
        Intent open = new Intent(this, MagicToggleActivity.class).setAction(Intent.ACTION_MAIN);
        PendingIntent content = PendingIntent.getActivity(
                this,
                0,
                open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        // A direct local service STOP would bypass the phone's archive drain
        // transaction. Route the notification action through the same visible
        // MAGIC toggle bridge used by the watch-face complication.
        Intent stop = new Intent(this, MagicToggleActivity.class)
                .setAction(WearActionReceiver.ACTION_MAGIC_TOGGLE);
        PendingIntent stopAction = PendingIntent.getActivity(
                this,
                82_001,
                stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_shushunya)
                .setContentTitle("Шушуня · микрофон Watch6")
                .setContentText(status)
                .setContentIntent(content)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .addAction(new Notification.Action.Builder(
                        null, "Остановить", stopAction).build())
                .build();
    }

    @SuppressLint("WakelockTimeout")
    private void acquireWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) return;
        PowerManager manager = getSystemService(PowerManager.class);
        if (manager == null) return;
        wakeLock = manager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK, "Shushunya:WatchMicUplink");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire();
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        wakeLock = null;
    }

    private static String bounded(String value, int maxLength) {
        String clean = clean(value);
        return clean.length() <= maxLength ? clean : "";
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static final class BuiltInCaptureSetup {
        final SessionResources resources;
        final AudioRecord record;
        final AudioDeviceInfo builtInInput;
        final StartupPcmFrame startup;
        final int reacquisitions;

        BuiltInCaptureSetup(
                SessionResources resources,
                AudioRecord record,
                AudioDeviceInfo builtInInput,
                StartupPcmFrame startup,
                int reacquisitions) {
            this.resources = resources;
            this.record = record;
            this.builtInInput = builtInInput;
            this.startup = startup;
            this.reacquisitions = reacquisitions;
        }
    }

    private static final class StartupPcmFrame {
        final long frameEndPosition;
        final long readDoneBootNanos;
        final short[] samples;
        final byte[] pcmLittleEndian;

        StartupPcmFrame(
                long frameEndPosition,
                long readDoneBootNanos,
                short[] samples) {
            if (frameEndPosition <= 0L || readDoneBootNanos <= 0L
                    || samples == null
                    || samples.length != WearAudioProtocol.FRAME_SAMPLES) {
                throw new IllegalArgumentException("invalid startup PCM frame");
            }
            this.frameEndPosition = frameEndPosition;
            this.readDoneBootNanos = readDoneBootNanos;
            this.samples = samples.clone();
            this.pcmLittleEndian = new byte[WearAudioProtocol.PCM_BYTES_PER_FRAME];
            for (int index = 0; index < this.samples.length; index++) {
                int value = this.samples[index];
                pcmLittleEndian[index * 2] = (byte) (value & 0xff);
                pcmLittleEndian[index * 2 + 1] = (byte) ((value >>> 8) & 0xff);
            }
        }
    }

    private static final class ZeroPcmReacquireRequired extends Exception {}

    private static final class WatchBuiltInZeroPcmException extends Exception {
        WatchBuiltInZeroPcmException(String message) {
            super(message);
        }
    }

    private static final class SessionResources implements AutoCloseable {
        final Context context;
        final AudioManager manager;
        final int previousMode;
        final AudioDeviceInfo previousCommunicationDevice;
        private final AtomicBoolean closed = new AtomicBoolean(false);
        private final AtomicBoolean captureStarted = new AtomicBoolean(false);
        private volatile AudioRecord record;
        private volatile OutputStream output;
        private volatile ChannelClient.Channel channel;

        SessionResources(
                Context context,
                AudioManager manager,
                int previousMode,
                AudioDeviceInfo previousCommunicationDevice) {
            this.context = context;
            this.manager = manager;
            this.previousMode = previousMode;
            this.previousCommunicationDevice = previousCommunicationDevice;
        }

        void setRecord(AudioRecord value) {
            record = value;
            if (closed.get()) {
                try { value.stop(); } catch (Exception ignored) {}
                value.release();
                throw new IllegalStateException("Watch microphone record already stopped");
            }
        }

        void markCaptureStarted() {
            captureStarted.set(true);
        }

        boolean captureStarted() {
            return captureStarted.get();
        }

        void requestProducerStop() {
            AudioRecord active = record;
            if (active == null) return;
            try { active.stop(); } catch (Exception ignored) {}
        }

        synchronized void installTransport(
                ChannelClient.Channel valueChannel, OutputStream valueOutput) {
            if (closed.get()) {
                closeLooseOutput(valueOutput);
                try { Wearable.getChannelClient(context).close(valueChannel); }
                catch (Exception ignored) {}
                throw new IllegalStateException("Watch microphone resources already closed");
            }
            closeTransport();
            channel = valueChannel;
            output = valueOutput;
        }

        synchronized void closeTransport() {
            OutputStream activeOutput = output;
            output = null;
            if (activeOutput != null) closeLooseOutput(activeOutput);
            ChannelClient.Channel activeChannel = channel;
            channel = null;
            if (activeChannel != null) {
                try { Wearable.getChannelClient(context).close(activeChannel); }
                catch (Exception ignored) {}
            }
        }

        @Override
        public void close() {
            if (!closed.compareAndSet(false, true)) return;
            // Required teardown order: capture -> global route -> transport.
            AudioRecord activeRecord = record;
            record = null;
            if (activeRecord != null) {
                try { activeRecord.stop(); } catch (Exception ignored) {}
                try { activeRecord.release(); } catch (Exception ignored) {}
            }
            if (manager != null) {
                try { manager.clearCommunicationDevice(); } catch (Exception ignored) {}
                if (previousCommunicationDevice != null) {
                    try { manager.setCommunicationDevice(previousCommunicationDevice); }
                    catch (Exception ignored) {}
                }
                try { manager.setMode(previousMode); } catch (Exception ignored) {}
            }
            closeTransport();
        }
    }

    private static final class ChannelWriter {
        final long sessionId;
        final DataOutputStream output;

        ChannelWriter(long sessionId, DataOutputStream output) {
            this.sessionId = sessionId;
            this.output = output;
        }
    }

    private static final class ReconnectResult {
        final ChannelWriter writer;
        final int nextAttemptIndex;

        ReconnectResult(ChannelWriter writer, int nextAttemptIndex) {
            this.writer = writer;
            this.nextAttemptIndex = nextAttemptIndex;
        }
    }

    private static final class StreamResult {
        final long sessionId;
        final long lastFlushedSequence;
        final long droppedFrames;

        StreamResult(long sessionId, long lastFlushedSequence, long droppedFrames) {
            this.sessionId = sessionId;
            this.lastFlushedSequence = lastFlushedSequence;
            this.droppedFrames = droppedFrames;
        }
    }

    private static final class AudioFrame {
        final int sequence;
        final long captureElapsedNanos;
        final int flags;
        final byte[] pcmLittleEndian;

        AudioFrame(
                int sequence,
                long captureElapsedNanos,
                int flags,
                byte[] pcmLittleEndian) {
            this.sequence = sequence;
            this.captureElapsedNanos = captureElapsedNanos;
            this.flags = flags;
            this.pcmLittleEndian = pcmLittleEndian;
        }
    }

    private static final class PendingAudioFrame {
        final long frameEndPosition;
        final long readDoneBootNanos;
        final byte[] pcmLittleEndian;

        PendingAudioFrame(
                long frameEndPosition,
                long readDoneBootNanos,
                byte[] pcmLittleEndian) {
            this.frameEndPosition = frameEndPosition;
            this.readDoneBootNanos = readDoneBootNanos;
            this.pcmLittleEndian = pcmLittleEndian;
        }
    }
}
