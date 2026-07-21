package com.shushunya.m.wear.audio;

import android.Manifest;
import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothHeadset;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.SystemClock;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** Owns one exact PowerConf HFP profile session on the Watch. */
public final class BluetoothPowerConfVoiceSession {
    private static final long POLL_MILLIS = 50L;
    private static final long CLOSE_WAIT_MILLIS = 1_000L;

    private BluetoothPowerConfVoiceSession() {}

    public static CompletableFuture<OpenResult> open(
            Context context,
            ScheduledExecutorService scheduler,
            long timeoutMillis) {
        Objects.requireNonNull(context, "context");
        Objects.requireNonNull(scheduler, "scheduler");
        if (timeoutMillis <= 0L) {
            throw new IllegalArgumentException("timeoutMillis must be positive");
        }
        Acquisition acquisition = new Acquisition(
                context.getApplicationContext(), scheduler, timeoutMillis, true);
        acquisition.start();
        return acquisition.future;
    }

    /**
     * Proves the exact connected HFP identity but deliberately does not call
     * BluetoothHeadset.startVoiceRecognition(). The caller must activate SCO
     * through AudioManager.setCommunicationDevice() using an item returned by
     * getAvailableCommunicationDevices().
     */
    public static CompletableFuture<OpenResult> openRouteOnly(
            Context context,
            ScheduledExecutorService scheduler,
            long timeoutMillis) {
        Objects.requireNonNull(context, "context");
        Objects.requireNonNull(scheduler, "scheduler");
        if (timeoutMillis <= 0L) {
            throw new IllegalArgumentException("timeoutMillis must be positive");
        }
        Acquisition acquisition = new Acquisition(
                context.getApplicationContext(), scheduler, timeoutMillis, false);
        acquisition.start();
        return acquisition.future;
    }

    public enum Status {
        ACQUIRED,
        TIMEOUT,
        PERMISSION_DENIED,
        BLUETOOTH_UNAVAILABLE,
        BLUETOOTH_DISABLED,
        PROFILE_REQUEST_REJECTED,
        PROFILE_DISCONNECTED,
        TOPOLOGY_REJECTED,
        VOICE_RECOGNITION_UNSUPPORTED,
        START_REJECTED,
        TOPOLOGY_CHANGED,
        OTHER_AUDIO_CONNECTED,
        ERROR
    }

    public static final class OpenResult {
        public final Status status;
        public final Session session;
        public final List<PowerConfScoSessionPolicy.Device> connectedDevices;
        public final PowerConfScoSessionPolicy.Selection selection;
        public final boolean voiceRecognitionSupported;
        public final String detail;

        private OpenResult(
                Status status,
                Session session,
                List<PowerConfScoSessionPolicy.Device> connectedDevices,
                PowerConfScoSessionPolicy.Selection selection,
                boolean voiceRecognitionSupported,
                String detail) {
            this.status = status;
            this.session = session;
            this.connectedDevices = Collections.unmodifiableList(
                    new ArrayList<>(connectedDevices));
            this.selection = selection;
            this.voiceRecognitionSupported = voiceRecognitionSupported;
            this.detail = detail == null ? "" : detail;
        }

        public boolean isAcquired() {
            return status == Status.ACQUIRED && session != null;
        }
    }

    /** Owns the exact PowerConf HEADSET proxy and, only in legacy mode, its VR request. */
    public static final class Session implements AutoCloseable {
        private final BluetoothAdapter adapter;
        private final BluetoothHeadset headset;
        private final BluetoothDevice powerConf;
        private final PowerConfScoSessionPolicy.Device descriptor;
        private final boolean ownsVoiceRecognition;
        private final AtomicBoolean closed = new AtomicBoolean(false);

        private Session(
                BluetoothAdapter adapter,
                BluetoothHeadset headset,
                BluetoothDevice powerConf,
                PowerConfScoSessionPolicy.Device descriptor,
                boolean ownsVoiceRecognition) {
            this.adapter = adapter;
            this.headset = headset;
            this.powerConf = powerConf;
            this.descriptor = descriptor;
            this.ownsVoiceRecognition = ownsVoiceRecognition;
        }

        public PowerConfScoSessionPolicy.Device device() {
            return descriptor;
        }

        @SuppressLint("MissingPermission")
        public boolean isAudioConnected() {
            if (closed.get()) return false;
            try {
                return headset.isAudioConnected(powerConf);
            } catch (RuntimeException ignored) {
                return false;
            }
        }

        @Override
        @SuppressLint("MissingPermission")
        public void close() {
            if (!closed.compareAndSet(false, true)) return;
            try {
                if (ownsVoiceRecognition) {
                    try {
                        headset.stopVoiceRecognition(powerConf);
                    } catch (RuntimeException ignored) {
                        // Profile release remains mandatory.
                    }
                }
                long deadline = SystemClock.elapsedRealtime() + CLOSE_WAIT_MILLIS;
                while (SystemClock.elapsedRealtime() < deadline) {
                    boolean connected;
                    try {
                        connected = headset.isAudioConnected(powerConf);
                    } catch (RuntimeException failure) {
                        break;
                    }
                    if (!connected) break;
                    SystemClock.sleep(POLL_MILLIS);
                }
            } finally {
                try {
                    adapter.closeProfileProxy(BluetoothProfile.HEADSET, headset);
                } catch (RuntimeException ignored) {
                    // Binder may already be gone.
                }
            }
        }
    }

    private static final class Acquisition implements BluetoothProfile.ServiceListener {
        private final Context context;
        private final ScheduledExecutorService scheduler;
        private final long timeoutMillis;
        private final boolean requestVoiceRecognition;
        private final CompletableFuture<OpenResult> future = new CompletableFuture<>();
        private final AtomicReference<BluetoothHeadset> proxy = new AtomicReference<>();
        private final AtomicBoolean terminal = new AtomicBoolean(false);
        private final AtomicBoolean proxyClosed = new AtomicBoolean(false);
        private final AtomicReference<ScheduledFuture<?>> timeoutTask = new AtomicReference<>();
        private final AtomicReference<ScheduledFuture<?>> pollTask = new AtomicReference<>();

        private BluetoothAdapter adapter;
        private BluetoothDevice powerConf;
        private boolean voiceRecognitionStarted;
        private List<PowerConfScoSessionPolicy.Device> lastDevices = Collections.emptyList();
        private PowerConfScoSessionPolicy.Selection selection =
                PowerConfScoSessionPolicy.selectForStart(
                        Collections.emptyList(), PowerConfScoSessionPolicy.EXPECTED_ADDRESS);
        private boolean voiceRecognitionSupported;

        Acquisition(
                Context context,
                ScheduledExecutorService scheduler,
                long timeoutMillis,
                boolean requestVoiceRecognition) {
            this.context = context;
            this.scheduler = scheduler;
            this.timeoutMillis = timeoutMillis;
            this.requestVoiceRecognition = requestVoiceRecognition;
            future.whenComplete((result, error) -> {
                if (future.isCancelled()) {
                    finishFailure(Status.ERROR, "Acquisition cancelled by caller");
                }
            });
        }

        @SuppressLint("MissingPermission")
        void start() {
            if (context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                    != PackageManager.PERMISSION_GRANTED) {
                finishFailure(Status.PERMISSION_DENIED, "BLUETOOTH_CONNECT is not granted");
                return;
            }
            BluetoothManager manager = context.getSystemService(BluetoothManager.class);
            adapter = manager == null ? null : manager.getAdapter();
            if (adapter == null) {
                finishFailure(Status.BLUETOOTH_UNAVAILABLE, "BluetoothAdapter is unavailable");
                return;
            }

            try {
                if (!adapter.isEnabled()) {
                    finishFailure(Status.BLUETOOTH_DISABLED, "Bluetooth is disabled");
                    return;
                }
                timeoutTask.set(scheduler.schedule(
                        () -> finishFailure(
                                Status.TIMEOUT,
                                "PowerConf SCO timeout after " + timeoutMillis + " ms"),
                        timeoutMillis,
                        TimeUnit.MILLISECONDS));
                if (terminal.get()) cancel(timeoutTask);
                if (!adapter.getProfileProxy(context, this, BluetoothProfile.HEADSET)) {
                    finishFailure(
                            Status.PROFILE_REQUEST_REJECTED,
                            "BluetoothAdapter rejected HEADSET profile request");
                }
            } catch (SecurityException denied) {
                finishFailure(Status.PERMISSION_DENIED, describe(denied));
            } catch (RuntimeException failure) {
                finishFailure(Status.ERROR, describe(failure));
            }
        }

        @Override
        @SuppressLint("MissingPermission")
        public void onServiceConnected(int profile, BluetoothProfile profileProxy) {
            if (profile != BluetoothProfile.HEADSET
                    || !(profileProxy instanceof BluetoothHeadset)) {
                finishFailure(Status.ERROR, "Unexpected Bluetooth profile proxy: " + profile);
                return;
            }
            BluetoothHeadset headset = (BluetoothHeadset) profileProxy;
            if (!proxy.compareAndSet(null, headset)) {
                closeUnexpectedProxy(profileProxy);
                return;
            }
            if (terminal.get()) {
                closeProxyIfPresent();
                return;
            }

            try {
                List<BluetoothDevice> connected = headset.getConnectedDevices();
                lastDevices = snapshot(headset, connected);
                selection = PowerConfScoSessionPolicy.selectForStart(
                        lastDevices, PowerConfScoSessionPolicy.EXPECTED_ADDRESS);
                if (!selection.isReady()) {
                    finishFailure(
                            Status.TOPOLOGY_REJECTED,
                            "HFP topology rejected: " + selection.decision);
                    return;
                }
                powerConf = findExactDevice(connected, selection.device.address);
                if (powerConf == null) {
                    finishFailure(
                            Status.TOPOLOGY_CHANGED,
                            "Exact PowerConf disappeared before acquisition");
                    return;
                }

                voiceRecognitionSupported = headset.isVoiceRecognitionSupported(powerConf);
                if (!requestVoiceRecognition) {
                    finishSuccess(headset, selection.device, false,
                            "Exact PowerConf HFP identity proven; SCO is AudioManager-owned");
                    return;
                }
                if (!voiceRecognitionSupported) {
                    finishFailure(
                            Status.VOICE_RECOGNITION_UNSUPPORTED,
                            "Exact PowerConf reports no HFP voice-recognition support");
                    return;
                }
                voiceRecognitionStarted = headset.startVoiceRecognition(powerConf);
                if (terminal.get()) {
                    if (voiceRecognitionStarted) {
                        try {
                            headset.stopVoiceRecognition(powerConf);
                        } catch (RuntimeException ignored) {
                            // Failure path already owns cleanup.
                        }
                    }
                    closeProxyIfPresent();
                    return;
                }
                if (!voiceRecognitionStarted) {
                    finishFailure(
                            Status.START_REJECTED,
                            "startVoiceRecognition(exact PowerConf) returned false");
                    return;
                }
                pollTask.set(scheduler.scheduleWithFixedDelay(
                        this::pollAudioOwnership,
                        0L,
                        POLL_MILLIS,
                        TimeUnit.MILLISECONDS));
                if (terminal.get()) cancel(pollTask);
            } catch (SecurityException denied) {
                finishFailure(Status.PERMISSION_DENIED, describe(denied));
            } catch (RuntimeException failure) {
                finishFailure(Status.ERROR, describe(failure));
            }
        }

        @Override
        public void onServiceDisconnected(int profile) {
            if (profile == BluetoothProfile.HEADSET) {
                finishFailure(
                        Status.PROFILE_DISCONNECTED,
                        "HEADSET profile disconnected during acquisition");
            }
        }

        @SuppressLint("MissingPermission")
        private void pollAudioOwnership() {
            if (terminal.get()) return;
            BluetoothHeadset headset = proxy.get();
            if (headset == null || powerConf == null) {
                finishFailure(
                        Status.TOPOLOGY_CHANGED,
                        "HEADSET proxy or exact PowerConf disappeared");
                return;
            }
            try {
                List<BluetoothDevice> connected = headset.getConnectedDevices();
                lastDevices = snapshot(headset, connected);
                PowerConfScoSessionPolicy.AudioEvaluation audio =
                        PowerConfScoSessionPolicy.evaluateAudio(
                                lastDevices, PowerConfScoSessionPolicy.EXPECTED_ADDRESS);
                if (audio.decision
                        == PowerConfScoSessionPolicy.AudioDecision.OTHER_AUDIO_CONNECTED) {
                    finishFailure(
                            Status.OTHER_AUDIO_CONNECTED,
                            "Another connected HFP device owns SCO audio");
                    return;
                }
                if (audio.isExclusive()) {
                    finishSuccess(headset, audio.device, true,
                            "Exact PowerConf exclusively owns HFP SCO");
                } else if (audio.decision
                        == PowerConfScoSessionPolicy.AudioDecision.POWERCONF_MISSING
                        || audio.decision
                        == PowerConfScoSessionPolicy.AudioDecision.DUPLICATE_POWERCONF_ADDRESS) {
                    finishFailure(
                            Status.TOPOLOGY_CHANGED,
                            "HFP topology changed while opening SCO: " + audio.decision);
                }
            } catch (SecurityException denied) {
                finishFailure(Status.PERMISSION_DENIED, describe(denied));
            } catch (RuntimeException failure) {
                finishFailure(Status.ERROR, describe(failure));
            }
        }

        private void finishSuccess(
                BluetoothHeadset headset,
                PowerConfScoSessionPolicy.Device descriptor,
                boolean ownsVoiceRecognition,
                String detail) {
            if (!terminal.compareAndSet(false, true)) return;
            cancelTimers();
            Session session = new Session(
                    adapter, headset, powerConf, descriptor, ownsVoiceRecognition);
            boolean delivered = future.complete(new OpenResult(
                    Status.ACQUIRED,
                    session,
                    lastDevices,
                    selection,
                    voiceRecognitionSupported,
                    detail));
            if (!delivered) session.close();
        }

        @SuppressLint("MissingPermission")
        private void finishFailure(Status status, String detail) {
            if (!terminal.compareAndSet(false, true)) return;
            cancelTimers();
            BluetoothHeadset headset = proxy.get();
            if (voiceRecognitionStarted && headset != null && powerConf != null) {
                try {
                    headset.stopVoiceRecognition(powerConf);
                } catch (RuntimeException ignored) {
                    // Proxy release below remains mandatory.
                }
            }
            closeProxyIfPresent();
            if (!future.isDone()) {
                future.complete(new OpenResult(
                        status,
                        null,
                        lastDevices,
                        selection,
                        voiceRecognitionSupported,
                        detail));
            }
        }

        private void cancelTimers() {
            cancel(timeoutTask);
            cancel(pollTask);
        }

        private static void cancel(AtomicReference<ScheduledFuture<?>> reference) {
            ScheduledFuture<?> task = reference.getAndSet(null);
            if (task != null) task.cancel(false);
        }

        private void closeProxyIfPresent() {
            BluetoothHeadset headset = proxy.get();
            BluetoothAdapter currentAdapter = adapter;
            if (headset == null || currentAdapter == null
                    || !proxyClosed.compareAndSet(false, true)) return;
            try {
                currentAdapter.closeProfileProxy(BluetoothProfile.HEADSET, headset);
            } catch (RuntimeException ignored) {
                // Best effort if Android already disconnected the binder.
            }
        }

        private void closeUnexpectedProxy(BluetoothProfile unexpected) {
            BluetoothAdapter currentAdapter = adapter;
            if (currentAdapter == null) return;
            try {
                currentAdapter.closeProfileProxy(BluetoothProfile.HEADSET, unexpected);
            } catch (RuntimeException ignored) {
                // Duplicate callback cleanup only.
            }
        }

        @SuppressLint("MissingPermission")
        private static List<PowerConfScoSessionPolicy.Device> snapshot(
                BluetoothHeadset headset, List<BluetoothDevice> connected) {
            List<PowerConfScoSessionPolicy.Device> result = new ArrayList<>();
            if (connected == null) return result;
            for (BluetoothDevice device : connected) {
                if (device == null) continue;
                result.add(new PowerConfScoSessionPolicy.Device(
                        device.getName(),
                        device.getAddress(),
                        device.getBondState(),
                        headset.isAudioConnected(device)));
            }
            return result;
        }

        @SuppressLint("MissingPermission")
        private static BluetoothDevice findExactDevice(
                List<BluetoothDevice> devices, String address) {
            BluetoothDevice match = null;
            if (devices == null) return null;
            for (BluetoothDevice device : devices) {
                if (device == null || !address.equalsIgnoreCase(device.getAddress())) continue;
                if (match != null) return null;
                match = device;
            }
            return match;
        }

        private static String describe(Throwable failure) {
            String message = failure.getMessage();
            return failure.getClass().getSimpleName() + ": "
                    + (message == null ? "" : message);
        }
    }
}
