package com.shushunya.m.wear.audio;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioRecordingConfiguration;
import android.media.MediaRecorder;
import android.media.MicrophoneInfo;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import com.shushunya.m.R;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * ADB-only, local PowerConf capture probe. It never opens a Wear channel and
 * never sends audio to the phone or server. Every AudioRecord is pinned to the
 * one exact PowerConf HFP/SCO input after exclusive physical ownership is proven.
 */
public final class PowerConfRawDiagnosticService extends Service {
    public static final String ACTION_CAPTURE =
            "com.shushunya.m.wear.audio.DIAGNOSTIC_RAW_CAPTURE";
    public static final String EXTRA_SECONDS_PER_SOURCE = "seconds_per_source";
    public static final int DEFAULT_SECONDS_PER_SOURCE = 4;

    private static final String TAG = "ShushunyaPowerConfDiag";
    private static final String CHANNEL_ID = "shushunya_powerconf_diagnostic";
    private static final int NOTIFICATION_ID = 1864;
    private static final int SAMPLE_RATE = 16_000;
    private static final int FRAME_SAMPLES = 320;
    private static final int MIN_SECONDS = 2;
    private static final int MAX_SECONDS = 15;
    private static final long SCO_OPEN_TIMEOUT_MS = 12_000L;
    private static final long ROUTE_TIMEOUT_MS = 6_000L;

    private static final SourceSpec[] SOURCES = {
            new SourceSpec("voice_communication", MediaRecorder.AudioSource.VOICE_COMMUNICATION),
            new SourceSpec("voice_recognition", MediaRecorder.AudioSource.VOICE_RECOGNITION),
            new SourceSpec("mic", MediaRecorder.AudioSource.MIC)
    };

    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicBoolean stopRequested = new AtomicBoolean(false);
    private volatile Thread worker;
    private volatile CaptureOwner activeOwner;
    private PowerManager.WakeLock wakeLock;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager notifications = getSystemService(NotificationManager.class);
        if (notifications != null) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "PowerConf raw diagnostics",
                    NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("Local exact-PowerConf PCM source matrix");
            notifications.createNotificationChannel(channel);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? "" : clean(intent.getAction());
        if (!ACTION_CAPTURE.equals(action)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        int requested = intent.getIntExtra(
                EXTRA_SECONDS_PER_SOURCE, DEFAULT_SECONDS_PER_SOURCE);
        int secondsPerSource = Math.max(MIN_SECONDS, Math.min(MAX_SECONDS, requested));
        startDiagnosticForeground("Preparing exact PowerConf raw capture…");

        if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                != PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            finishWithStatus("Diagnostic refused: microphone/Bluetooth permission missing");
            return START_NOT_STICKY;
        }
        if (WearMicForegroundService.isCaptureServiceActive()) {
            finishWithStatus("Diagnostic refused: translator capture is active");
            return START_NOT_STICKY;
        }
        if (!running.compareAndSet(false, true)) {
            updateNotification("PowerConf diagnostic already running");
            return START_NOT_STICKY;
        }

        stopRequested.set(false);
        acquireWakeLock();
        worker = new Thread(() -> runDiagnostic(secondsPerSource), "powerconf-raw-diag");
        worker.start();
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        stopRequested.set(true);
        CaptureOwner owner = activeOwner;
        activeOwner = null;
        if (owner != null) owner.close();
        Thread active = worker;
        worker = null;
        if (active != null && active != Thread.currentThread()) active.interrupt();
        releaseWakeLock();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void runDiagnostic(int secondsPerSource) {
        CaptureOwner owner = null;
        JSONObject summary = new JSONObject();
        File runDirectory = null;
        String conclusion = "INCOMPLETE";
        try {
            runDirectory = createRunDirectory();
            summary.put("format", "shushunya-powerconf-raw-diagnostic-v2");
            summary.put("route_mode", "modern_audio_manager_only");
            summary.put("legacy_start_voice_recognition_called", false);
            summary.put("created_at_epoch_ms", System.currentTimeMillis());
            summary.put("expected_name", PowerConfScoSessionPolicy.EXPECTED_NAME);
            summary.put("expected_address", PowerConfScoSessionPolicy.EXPECTED_ADDRESS);
            summary.put("sample_rate_hz", SAMPLE_RATE);
            summary.put("channels", 1);
            summary.put("encoding", "pcm_s16le");
            summary.put("seconds_per_source", secondsPerSource);

            owner = acquireExactPowerConf();
            activeOwner = owner;
            summary.put("sco_input_id", owner.input.getId());
            summary.put("sco_input_address", clean(owner.input.getAddress()));
            summary.put("sco_output_id", owner.output.getId());
            summary.put("sco_output_address", clean(owner.output.getAddress()));
            summary.put("system_microphone_mute", safeSystemMute(owner.manager));

            JSONArray captures = new JSONArray();
            int allZero = 0;
            int signalled = 0;
            for (SourceSpec source : SOURCES) {
                requireRunning();
                updateNotification("Raw probe: " + source.name);
                CaptureResult result = captureSource(owner, source, secondsPerSource, runDirectory);
                captures.put(result.json);
                if (result.allZero) allZero++;
                else signalled++;
                SystemClock.sleep(150L);
            }
            summary.put("captures", captures);
            if (allZero == SOURCES.length) {
                conclusion = "ALL_SOURCES_DIGITAL_ZERO_ACCESSORY_OR_HFP_UPLINK_MUTED";
            } else if (captures.getJSONObject(0).getBoolean("all_zero")) {
                conclusion = "VOICE_COMMUNICATION_PATH_ZERO_ALTERNATE_SOURCE_HAS_SIGNAL";
            } else if (signalled == SOURCES.length) {
                conclusion = "ALL_SOURCES_HAVE_SIGNAL";
            } else {
                conclusion = "PARTIAL_SOURCE_SIGNAL";
            }
            summary.put("conclusion", conclusion);
            summary.put("completed", true);
            writeJson(new File(runDirectory, "summary.json"), summary);
            Log.i(TAG, "Raw diagnostic complete conclusion=" + conclusion
                    + " directory=" + runDirectory.getAbsolutePath());
            updateNotification("PowerConf probe saved: " + conclusion);
        } catch (Throwable failure) {
            Log.e(TAG, "Raw diagnostic failed", failure);
            try {
                summary.put("completed", false);
                summary.put("conclusion", conclusion);
                summary.put("error", failure.getClass().getSimpleName()
                        + ": " + clean(failure.getMessage()));
                if (runDirectory != null) {
                    writeJson(new File(runDirectory, "summary.json"), summary);
                }
            } catch (Exception ignored) {}
            updateNotification("PowerConf probe failed: " + clean(failure.getMessage()));
        } finally {
            activeOwner = null;
            if (owner != null) owner.close();
            running.set(false);
            stopRequested.set(false);
            releaseWakeLock();
            stopSelf();
        }
    }

    @SuppressLint("MissingPermission")
    private CaptureOwner acquireExactPowerConf() throws Exception {
        AudioManager manager = getSystemService(AudioManager.class);
        if (manager == null) throw new IllegalStateException("AudioManager unavailable");
        ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
        CaptureOwner owner = new CaptureOwner(
                manager, manager.getMode(), manager.getCommunicationDevice(), scheduler);
        try {
            CompletableFuture<BluetoothPowerConfVoiceSession.OpenResult> opening =
                    BluetoothPowerConfVoiceSession.openRouteOnly(
                            this, scheduler, SCO_OPEN_TIMEOUT_MS);
            owner.opening = opening;
            BluetoothPowerConfVoiceSession.OpenResult opened = opening.get(
                    SCO_OPEN_TIMEOUT_MS + 2_000L, TimeUnit.MILLISECONDS);
            if (!opened.isAcquired()) {
                throw new IllegalStateException(
                        "Exact PowerConf HFP unavailable: " + opened.status
                                + " · " + opened.detail);
            }
            owner.voice = opened.session;
            requireRunning();
            if (!PowerConfScoSessionPolicy.EXPECTED_ADDRESS.equalsIgnoreCase(
                    opened.session.device().address)
                    || !PowerConfScoSessionPolicy.isExactProduct(
                    opened.session.device().name)) {
                throw new IllegalStateException("Exact PowerConf identity changed");
            }

            AudioDeviceInfo output = awaitAvailableCommunicationSco(manager);
            manager.setMode(AudioManager.MODE_IN_COMMUNICATION);
            if (!manager.setCommunicationDevice(output)) {
                throw new IllegalStateException("Watch rejected exact PowerConf SCO output");
            }
            awaitCommunicationRoute(manager, output, owner.voice);
            owner.output = output;
            owner.input = awaitSoleScoInput(manager);
            return owner;
        } catch (Exception failure) {
            owner.close();
            throw failure;
        }
    }

    private CaptureResult captureSource(
            CaptureOwner owner, SourceSpec source, int seconds, File directory) throws Exception {
        requireExactRoute(owner, null);
        int minBuffer = AudioRecord.getMinBufferSize(
                SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minBuffer <= 0) throw new IllegalStateException("No 16 kHz AudioRecord buffer");
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            throw new SecurityException("RECORD_AUDIO revoked before diagnostic AudioRecord");
        }
        AudioRecord record = new AudioRecord.Builder()
                .setAudioSource(source.audioSource)
                .setAudioFormat(new AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(SAMPLE_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .build())
                .setBufferSizeInBytes(Math.max(minBuffer * 2, SAMPLE_RATE * 2))
                .build();
        owner.record = record;
        File pcmFile = new File(directory, source.name + "_s16le_16000_mono.pcm");
        File wavFile = new File(directory, source.name + "_s16le_16000_mono.wav");
        JSONObject details = new JSONObject();
        PowerConfDiagnosticStats stats = new PowerConfDiagnosticStats();
        long started = SystemClock.elapsedRealtime();
        long firstNonzeroMs = -1L;
        long targetSamples = (long) SAMPLE_RATE * seconds;
        long capturedSamples = 0L;
        int audioSessionId = -1;
        try (OutputStream output = new BufferedOutputStream(new FileOutputStream(pcmFile))) {
            if (record.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IllegalStateException(source.name + " AudioRecord not initialized");
            }
            if (!record.setPreferredDevice(owner.input)) {
                throw new IllegalStateException(source.name + " rejected exact PowerConf input");
            }
            record.startRecording();
            if (record.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                throw new IllegalStateException(source.name + " did not enter recording state");
            }
            AudioDeviceInfo routed = awaitRecordRoute(record, owner.input);
            requireExactRoute(owner, routed);
            audioSessionId = record.getAudioSessionId();
            details.put("active_microphones", activeMicrophones(record));
            details.put("active_recording_configuration", recordingConfiguration(record));
            details.put("all_active_recording_configurations",
                    activeRecordingConfigurations(owner.manager));

            short[] samples = new short[FRAME_SAMPLES];
            byte[] bytes = new byte[FRAME_SAMPLES * 2];
            int frames = 0;
            while (capturedSamples < targetSamples) {
                requireRunning();
                int wanted = (int) Math.min(samples.length, targetSamples - capturedSamples);
                int read = record.read(samples, 0, wanted, AudioRecord.READ_BLOCKING);
                if (read == AudioRecord.ERROR_DEAD_OBJECT) {
                    throw new IllegalStateException(source.name + " exact SCO disconnected");
                }
                if (read < 0) {
                    throw new IllegalStateException(source.name + " AudioRecord error " + read);
                }
                if (read == 0) continue;
                stats.observe(samples, 0, read);
                if (firstNonzeroMs < 0L && stats.nonzeroSamples() > 0L) {
                    firstNonzeroMs = SystemClock.elapsedRealtime() - started;
                }
                for (int index = 0; index < read; index++) {
                    int value = samples[index];
                    bytes[index * 2] = (byte) (value & 0xff);
                    bytes[index * 2 + 1] = (byte) ((value >>> 8) & 0xff);
                }
                output.write(bytes, 0, read * 2);
                capturedSamples += read;
                if ((++frames % 25) == 0) requireExactRoute(owner, record.getRoutedDevice());
            }
        } finally {
            try { record.stop(); } catch (Exception ignored) {}
            try { record.release(); } catch (Exception ignored) {}
            owner.record = null;
        }
        writeWav(pcmFile, wavFile, capturedSamples);

        details.put("source_name", source.name);
        details.put("audio_source", source.audioSource);
        details.put("audio_session_id", audioSessionId);
        details.put("requested_seconds", seconds);
        details.put("captured_samples", capturedSamples);
        details.put("captured_bytes", capturedSamples * 2L);
        details.put("elapsed_ms", SystemClock.elapsedRealtime() - started);
        details.put("nonzero_samples", stats.nonzeroSamples());
        details.put("all_zero", stats.isAllZero());
        details.put("peak_abs", stats.peak());
        details.put("rms", stats.rms());
        details.put("clipped_samples", stats.clippedSamples());
        details.put("first_nonzero_ms", firstNonzeroMs);
        details.put("pcm_file", pcmFile.getName());
        details.put("wav_file", wavFile.getName());
        return new CaptureResult(details, stats.isAllZero());
    }

    @SuppressLint("MissingPermission")
    private static JSONArray activeMicrophones(AudioRecord record) {
        JSONArray result = new JSONArray();
        try {
            for (MicrophoneInfo microphone : record.getActiveMicrophones()) {
                JSONObject item = new JSONObject();
                item.put("id", microphone.getId());
                item.put("type", microphone.getType());
                item.put("address", clean(microphone.getAddress()));
                item.put("description", String.valueOf(microphone.getDescription()));
                item.put("location", microphone.getLocation());
                item.put("directionality", microphone.getDirectionality());
                result.put(item);
            }
        } catch (Exception failure) {
            result.put("unavailable: " + failure.getClass().getSimpleName());
        }
        return result;
    }

    private static JSONObject recordingConfiguration(AudioRecord record) {
        JSONObject result = new JSONObject();
        try {
            AudioRecordingConfiguration configuration =
                    record.getActiveRecordingConfiguration();
            if (configuration == null) {
                result.put("present", false);
                return result;
            }
            result.put("present", true);
            appendRecordingConfiguration(result, configuration);
        } catch (Exception failure) {
            try { result.put("error", failure.getClass().getSimpleName()); }
            catch (Exception ignored) {}
        }
        return result;
    }

    @SuppressLint("MissingPermission")
    private static JSONArray activeRecordingConfigurations(AudioManager manager) {
        JSONArray result = new JSONArray();
        try {
            for (AudioRecordingConfiguration configuration
                    : manager.getActiveRecordingConfigurations()) {
                JSONObject item = new JSONObject();
                appendRecordingConfiguration(item, configuration);
                result.put(item);
            }
        } catch (Exception failure) {
            result.put("unavailable: " + failure.getClass().getSimpleName());
        }
        return result;
    }

    private static void appendRecordingConfiguration(
            JSONObject target, AudioRecordingConfiguration configuration) throws Exception {
        target.put("client_silenced", configuration.isClientSilenced());
        target.put("client_audio_source", configuration.getClientAudioSource());
        target.put("client_session_id", configuration.getClientAudioSessionId());
        AudioDeviceInfo device = configuration.getAudioDevice();
        target.put("device", endpoint(device));
    }

    private AudioDeviceInfo awaitAvailableCommunicationSco(AudioManager manager)
            throws Exception {
        long deadline = SystemClock.elapsedRealtime() + ROUTE_TIMEOUT_MS;
        ScoRoutePublicationPolicy.State last = ScoRoutePublicationPolicy.State.MISSING;
        while (SystemClock.elapsedRealtime() < deadline) {
            requireRunning();
            AudioDeviceInfo selected = null;
            List<String> addresses = new ArrayList<>();
            for (AudioDeviceInfo device : manager.getAvailableCommunicationDevices()) {
                if (device == null
                        || device.getType() != AudioDeviceInfo.TYPE_BLUETOOTH_SCO
                        || !device.isSink()) continue;
                selected = device;
                addresses.add(clean(device.getAddress()));
            }
            last = ScoRoutePublicationPolicy.evaluate(
                    addresses, PowerConfScoSessionPolicy.EXPECTED_ADDRESS);
            if (last == ScoRoutePublicationPolicy.State.READY) return selected;
            SystemClock.sleep(50L);
        }
        throw new IllegalStateException(
                "Exact sole PowerConf unavailable as communication device: " + last);
    }

    private AudioDeviceInfo awaitSoleScoInput(AudioManager manager) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + ROUTE_TIMEOUT_MS;
        ScoRoutePublicationPolicy.State last = ScoRoutePublicationPolicy.State.MISSING;
        while (SystemClock.elapsedRealtime() < deadline) {
            requireRunning();
            AudioDeviceInfo selected = null;
            List<String> addresses = new ArrayList<>();
            for (AudioDeviceInfo device : manager.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
                if (device == null
                        || device.getType() != AudioDeviceInfo.TYPE_BLUETOOTH_SCO
                        || !device.isSource()) continue;
                selected = device;
                addresses.add(clean(device.getAddress()));
            }
            last = ScoRoutePublicationPolicy.evaluate(
                    addresses, PowerConfScoSessionPolicy.EXPECTED_ADDRESS);
            if (last == ScoRoutePublicationPolicy.State.READY) return selected;
            SystemClock.sleep(50L);
        }
        throw new IllegalStateException(
                "Exact sole PowerConf SCO input not published: " + last);
    }

    private void awaitCommunicationRoute(
            AudioManager manager,
            AudioDeviceInfo expected,
            BluetoothPowerConfVoiceSession.Session voice) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + ROUTE_TIMEOUT_MS;
        while (SystemClock.elapsedRealtime() < deadline) {
            requireRunning();
            if (sameEndpoint(expected, manager.getCommunicationDevice())
                    && voice != null
                    && voice.isAudioConnected()) return;
            SystemClock.sleep(50L);
        }
        throw new IllegalStateException(
                "AudioManager route set but exact PowerConf HFP audio never activated");
    }

    private AudioDeviceInfo awaitRecordRoute(AudioRecord record, AudioDeviceInfo expected)
            throws Exception {
        long deadline = SystemClock.elapsedRealtime() + ROUTE_TIMEOUT_MS;
        AudioDeviceInfo last = null;
        while (SystemClock.elapsedRealtime() < deadline) {
            requireRunning();
            last = record.getRoutedDevice();
            if (sameEndpoint(expected, last)) return last;
            SystemClock.sleep(50L);
        }
        throw new IllegalStateException("AudioRecord did not route to exact PowerConf: "
                + endpoint(last));
    }

    private void requireExactRoute(CaptureOwner owner, AudioDeviceInfo routedInput)
            throws Exception {
        requireRunning();
        if (owner.voice == null || !owner.voice.isAudioConnected()) {
            throw new IllegalStateException("Exact PowerConf lost exclusive HFP/SCO ownership");
        }
        if (!sameEndpoint(owner.output, owner.manager.getCommunicationDevice())) {
            throw new IllegalStateException("PowerConf communication route changed");
        }
        if (routedInput != null && !sameEndpoint(owner.input, routedInput)) {
            throw new IllegalStateException("PowerConf AudioRecord route changed to "
                    + endpoint(routedInput));
        }
    }

    private void requireRunning() throws InterruptedException {
        if (stopRequested.get() || Thread.currentThread().isInterrupted()) {
            throw new InterruptedException("PowerConf diagnostic cancelled");
        }
    }

    private File createRunDirectory() throws Exception {
        File base = getExternalFilesDir("diagnostics");
        if (base == null) base = new File(getFilesDir(), "diagnostics");
        String timestamp = new SimpleDateFormat(
                "yyyyMMdd-HHmmss-SSS", Locale.US).format(new Date());
        File directory = new File(base, "powerconf-" + timestamp);
        if (!directory.mkdirs() && !directory.isDirectory()) {
            throw new IllegalStateException("Could not create " + directory);
        }
        return directory;
    }

    private static void writeJson(File file, JSONObject json) throws Exception {
        try (OutputStream output = new BufferedOutputStream(new FileOutputStream(file))) {
            output.write(json.toString(2).getBytes(StandardCharsets.UTF_8));
            output.write('\n');
        }
    }

    private static void writeWav(File pcm, File wav, long samples) throws Exception {
        long dataBytesLong = samples * 2L;
        if (dataBytesLong > 0xfffffff0L) {
            throw new IllegalStateException("Diagnostic PCM is too large for WAV");
        }
        int dataBytes = (int) dataBytesLong;
        try (OutputStream output = new BufferedOutputStream(new FileOutputStream(wav));
             BufferedInputStream input = new BufferedInputStream(new FileInputStream(pcm))) {
            output.write(new byte[] {'R', 'I', 'F', 'F'});
            writeLe32(output, 36 + dataBytes);
            output.write(new byte[] {'W', 'A', 'V', 'E', 'f', 'm', 't', ' '});
            writeLe32(output, 16);
            writeLe16(output, 1);
            writeLe16(output, 1);
            writeLe32(output, SAMPLE_RATE);
            writeLe32(output, SAMPLE_RATE * 2);
            writeLe16(output, 2);
            writeLe16(output, 16);
            output.write(new byte[] {'d', 'a', 't', 'a'});
            writeLe32(output, dataBytes);
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                if (read > 0) output.write(buffer, 0, read);
            }
        }
    }

    private static void writeLe16(OutputStream output, int value) throws Exception {
        output.write(value & 0xff);
        output.write((value >>> 8) & 0xff);
    }

    private static void writeLe32(OutputStream output, int value) throws Exception {
        output.write(value & 0xff);
        output.write((value >>> 8) & 0xff);
        output.write((value >>> 16) & 0xff);
        output.write((value >>> 24) & 0xff);
    }

    private void startDiagnosticForeground(String text) {
        Notification notification = buildNotification(text);
        startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
                        | ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
    }

    private void updateNotification(String text) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.notify(NOTIFICATION_ID, buildNotification(text));
    }

    private Notification buildNotification(String text) {
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_shushunya)
                .setContentTitle("Shushunya · PowerConf raw probe")
                .setContentText(text)
                .setOngoing(running.get())
                .setOnlyAlertOnce(true)
                .build();
    }

    private void finishWithStatus(String status) {
        Log.w(TAG, status);
        updateNotification(status);
        stopSelf();
    }

    private void acquireWakeLock() {
        PowerManager manager = getSystemService(PowerManager.class);
        if (manager == null) return;
        PowerManager.WakeLock lock = manager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK, "Shushunya:PowerConfRawDiagnostic");
        lock.setReferenceCounted(false);
        lock.acquire(90_000L);
        wakeLock = lock;
    }

    private void releaseWakeLock() {
        PowerManager.WakeLock lock = wakeLock;
        wakeLock = null;
        if (lock != null && lock.isHeld()) lock.release();
    }

    private static boolean safeSystemMute(AudioManager manager) {
        try { return manager.isMicrophoneMute(); }
        catch (RuntimeException ignored) { return false; }
    }

    private static boolean sameEndpoint(AudioDeviceInfo expected, AudioDeviceInfo actual) {
        if (expected == null || actual == null || expected.getType() != actual.getType()) {
            return false;
        }
        if (expected.getId() == actual.getId()) return true;
        String expectedAddress = clean(expected.getAddress());
        String actualAddress = clean(actual.getAddress());
        return !expectedAddress.isEmpty() && expectedAddress.equalsIgnoreCase(actualAddress);
    }

    private static String endpoint(AudioDeviceInfo device) {
        return device == null ? "none" : "type=" + device.getType()
                + " id=" + device.getId() + " address=" + clean(device.getAddress());
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static final class SourceSpec {
        final String name;
        final int audioSource;

        SourceSpec(String name, int audioSource) {
            this.name = name;
            this.audioSource = audioSource;
        }
    }

    private static final class CaptureResult {
        final JSONObject json;
        final boolean allZero;

        CaptureResult(JSONObject json, boolean allZero) {
            this.json = json;
            this.allZero = allZero;
        }
    }

    private static final class CaptureOwner implements AutoCloseable {
        final AudioManager manager;
        final int previousMode;
        final AudioDeviceInfo previousCommunicationDevice;
        final ScheduledExecutorService scheduler;
        final AtomicBoolean closed = new AtomicBoolean(false);
        volatile CompletableFuture<BluetoothPowerConfVoiceSession.OpenResult> opening;
        volatile BluetoothPowerConfVoiceSession.Session voice;
        volatile AudioRecord record;
        volatile AudioDeviceInfo input;
        volatile AudioDeviceInfo output;

        CaptureOwner(
                AudioManager manager,
                int previousMode,
                AudioDeviceInfo previousCommunicationDevice,
                ScheduledExecutorService scheduler) {
            this.manager = manager;
            this.previousMode = previousMode;
            this.previousCommunicationDevice = previousCommunicationDevice;
            this.scheduler = scheduler;
        }

        @Override
        public void close() {
            if (!closed.compareAndSet(false, true)) return;
            CompletableFuture<BluetoothPowerConfVoiceSession.OpenResult> pending = opening;
            if (pending != null && !pending.isDone()) pending.cancel(true);
            AudioRecord activeRecord = record;
            record = null;
            if (activeRecord != null) {
                try { activeRecord.stop(); } catch (Exception ignored) {}
                try { activeRecord.release(); } catch (Exception ignored) {}
            }
            try { manager.clearCommunicationDevice(); } catch (Exception ignored) {}
            if (previousCommunicationDevice != null) {
                try { manager.setCommunicationDevice(previousCommunicationDevice); }
                catch (Exception ignored) {}
            }
            try { manager.setMode(previousMode); } catch (Exception ignored) {}
            BluetoothPowerConfVoiceSession.Session activeVoice = voice;
            voice = null;
            if (activeVoice != null) activeVoice.close();
            scheduler.shutdownNow();
        }
    }
}
