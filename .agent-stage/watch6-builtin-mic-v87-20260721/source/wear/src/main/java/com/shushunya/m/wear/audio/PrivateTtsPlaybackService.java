package com.shushunya.m.wear.audio;

import android.Manifest;
import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioFocusRequest;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRouting;
import android.media.AudioTrack;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import androidx.annotation.Nullable;

import com.google.android.gms.tasks.Tasks;
import com.google.android.gms.wearable.ChannelClient;
import com.google.android.gms.wearable.Wearable;
import com.shushunya.m.R;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Process-owned, fail-closed private Russian TTS sink.
 *
 * <p>There is deliberately no MediaSession: the raw AudioTrack and low-importance
 * foreground notification must not create Samsung's unwanted media overlay.</p>
 */
public final class PrivateTtsPlaybackService extends Service {
    public static final String ACTION_ARM = "com.shushunya.m.wear.audio.PRIVATE_TTS_ARM";
    public static final String ACTION_STOP = "com.shushunya.m.wear.audio.PRIVATE_TTS_STOP";

    private static final String TAG = "ShushunyaPrivateTts";
    private static final String PREFS = "private_tts_playback_service_v1";
    private static final String KEY_ARMED = "armed";
    private static final String CHANNEL_ID = "shushunya_private_tts";
    private static final int NOTIFICATION_ID = 8219;
    private static final long ARM_WITHOUT_CHANNEL_TIMEOUT_MS = 30_000L;
    private static final Object OWNER_LOCK = new Object();
    private static final AtomicBoolean SERVICE_ACTIVE = new AtomicBoolean(false);
    private static volatile Owner owner;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private boolean explicitStop;
    private long armToken;

    /** Must be called while the command Activity is visibly user-launched. */
    public static void armFromVisibleActivity(Activity activity) {
        if (activity.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                != PackageManager.PERMISSION_GRANTED) {
            throw new SecurityException("BLUETOOTH_CONNECT not granted");
        }
        setPersistedArmed(activity, true);
        try {
            activity.startForegroundService(
                    new Intent(activity, PrivateTtsPlaybackService.class)
                            .setAction(ACTION_ARM));
        } catch (RuntimeException error) {
            setPersistedArmed(activity, false);
            throw error;
        }
    }

    public static boolean isArmed(Context context) {
        return SERVICE_ACTIVE.get() && persistedArmed(context);
    }

    public static void stop(Context context) {
        Context app = context.getApplicationContext();
        setPersistedArmed(app, false);
        Owner current;
        synchronized (OWNER_LOCK) {
            current = owner;
        }
        if (current != null) current.shutdown("service stopped");
        try {
            app.stopService(new Intent(app, PrivateTtsPlaybackService.class)
                    .setAction(ACTION_STOP));
        } catch (RuntimeException error) {
            Log.w(TAG, "Could not stop private TTS service", error);
        }
    }

    static boolean acceptChannel(Context context, ChannelClient.Channel channel) {
        Owner current;
        synchronized (OWNER_LOCK) {
            current = owner;
        }
        return current != null && isArmed(context) && current.accept(channel);
    }

    static void onPeerDisconnected(Context context, String nodeId) {
        Owner current;
        synchronized (OWNER_LOCK) {
            current = owner;
        }
        if (current != null) current.disconnectNode(nodeId);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_ARM : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            explicitStop = true;
            setPersistedArmed(this, false);
            shutdownOwner("explicit stop");
            stopSelfResult(startId);
            return START_NOT_STICKY;
        }

        startForeground(
                NOTIFICATION_ID,
                notification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        if (!persistedArmed(this)
                || checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                != PackageManager.PERMISSION_GRANTED) {
            explicitStop = true;
            setPersistedArmed(this, false);
            stopSelfResult(startId);
            return START_NOT_STICKY;
        }

        SERVICE_ACTIVE.set(true);
        synchronized (OWNER_LOCK) {
            if (owner == null || owner.isClosed()) {
                owner = new Owner(getApplicationContext());
            }
        }
        scheduleArmWatchdog();
        return START_REDELIVER_INTENT;
    }

    @Override
    public void onTimeout(int startId, int fgsType) {
        terminalStop("foreground timeout");
    }

    @Override
    public void onDestroy() {
        SERVICE_ACTIVE.set(false);
        shutdownOwner("service destroyed");
        if (explicitStop) setPersistedArmed(this, false);
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void scheduleArmWatchdog() {
        long token = ++armToken;
        mainHandler.postDelayed(() -> {
            Owner current;
            synchronized (OWNER_LOCK) {
                current = owner;
            }
            if (token == armToken
                    && isArmed(this)
                    && current != null
                    && !current.hasReadyChannel()) {
                Log.w(TAG, "Private TTS arm expired before a channel became READY");
                terminalStop("arm timeout");
            }
        }, ARM_WITHOUT_CHANNEL_TIMEOUT_MS);
    }

    private void terminalStop(String reason) {
        Log.w(TAG, "Stopping private TTS: " + reason);
        explicitStop = true;
        setPersistedArmed(this, false);
        shutdownOwner(reason);
        stopSelf();
    }

    private void shutdownOwner(String reason) {
        Owner previous;
        synchronized (OWNER_LOCK) {
            previous = owner;
            owner = null;
        }
        if (previous != null) previous.shutdown(reason);
    }

    private void createNotificationChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Shushunya private voice",
                NotificationManager.IMPORTANCE_LOW);
        channel.setSound(null, null);
        channel.enableVibration(false);
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private Notification notification() {
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_shushunya)
                .setContentTitle("Shushunya")
                .setContentText("Русский голос → SoundForm")
                .setCategory(Notification.CATEGORY_SERVICE)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setLocalOnly(true)
                .setShowWhen(false)
                .build();
    }

    private static SharedPreferences prefs(Context context) {
        Context app = context.getApplicationContext();
        return app.createDeviceProtectedStorageContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static boolean persistedArmed(Context context) {
        return prefs(context).getBoolean(KEY_ARMED, false);
    }

    private static void setPersistedArmed(Context context, boolean armed) {
        prefs(context).edit().putBoolean(KEY_ARMED, armed).commit();
    }

    /** Owns channel workers independently from WearableListenerService lifecycle. */
    private static final class Owner {
        private final Context app;
        private final ExecutorService workers = Executors.newCachedThreadPool(runnable -> {
            Thread thread = new Thread(runnable, "private-tts-channel");
            thread.setDaemon(true);
            return thread;
        });
        private final Handler handshakeHandler = new Handler(Looper.getMainLooper());
        private final AtomicBoolean closed = new AtomicBoolean(false);
        private final java.util.Set<Session> sessions = ConcurrentHashMap.newKeySet();
        private final AtomicReference<Session> active = new AtomicReference<>();
        private final AtomicLong activeGeneration = new AtomicLong(Long.MIN_VALUE);

        Owner(Context app) {
            this.app = app;
        }

        boolean isClosed() {
            return closed.get();
        }

        boolean hasReadyChannel() {
            Session session = active.get();
            return session != null && session.ready.get() && !session.obsolete.get();
        }

        boolean accept(ChannelClient.Channel channel) {
            if (closed.get() || channel == null) return false;
            try {
                workers.execute(() -> runChannel(channel));
                return true;
            } catch (RejectedExecutionException error) {
                Log.w(TAG, "Private TTS worker rejected channel", error);
                return false;
            }
        }

        void disconnectNode(String nodeId) {
            Session session = active.get();
            if (session != null && session.nodeId.equals(clean(nodeId))) {
                session.closeAsObsolete();
                requestServiceStop(app, "phone peer disconnected");
            }
        }

        void shutdown(String reason) {
            if (!closed.compareAndSet(false, true)) return;
            handshakeHandler.removeCallbacksAndMessages(null);
            Session session = active.getAndSet(null);
            if (session != null) session.closeAsObsolete();
            for (Session pending : sessions) pending.closeAsObsolete();
            sessions.clear();
            workers.shutdownNow();
            Log.i(TAG, "Private TTS owner stopped: " + reason);
        }

        private void runChannel(ChannelClient.Channel channel) {
            Session session = new Session(app, channel);
            sessions.add(session);
            Runnable handshakeTimeout = () -> {
                if (session.ready.get() || session.obsolete.get()) return;
                Log.w(TAG, "Private TTS channel handshake timed out node=" + session.nodeId);
                session.closeAsObsolete();
                if (active.compareAndSet(session, null) || active.get() == null) {
                    requestServiceStop(app, "private TTS handshake timeout");
                }
            };
            handshakeHandler.postDelayed(handshakeTimeout, 20_000L);
            boolean claimed = false;
            boolean terminal = false;
            try {
                session.openStreams();
                PrivateTtsProtocol.Header header =
                        PrivateTtsProtocol.readPhoneHeader(session.input);
                session.generation = header.generation;
                if (header.generation <= 0L) {
                    throw new SinkException("PROTOCOL_ERROR generation");
                }
                PrivateTtsProtocol.writeAckHeader(session.output, header.generation);
                session.output.flush();
                session.ackHeaderWritten.set(true);
                if (!claim(session, header.generation)) {
                    session.sendError(0L, "OBSOLETE_STREAM generation");
                    return;
                }
                claimed = true;

                if (app.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                        != PackageManager.PERMISSION_GRANTED) {
                    throw new SinkException("ROUTE_REJECTED bluetooth_permission");
                }
                PrivateTtsSoundFormRoute.Result route =
                        PrivateTtsSoundFormRoute.resolve(app);
                if (!route.hasDevice()) throw new SinkException(route.error);
                session.expectedDevice = route.device;
                try (RoutePlayback playback = new RoutePlayback(app, route.device)) {
                    playback.openAndProve();
                    playback.parkBetweenClips();
                    Log.i(TAG, "Private TTS READY generation=" + header.generation
                            + " route=" + PrivateTtsSoundFormRoute.describe(route.device));
                    session.sendAck(PrivateTtsProtocol.ACK_READY, 0L, "SoundForm");
                    session.ready.set(true);
                    terminal = readRecords(session, playback);
                }
            } catch (PrivateTtsProtocol.ProtocolException error) {
                session.trySendError(
                        session.currentClipId, "PROTOCOL_ERROR " + safe(error));
            } catch (SinkException error) {
                session.trySendError(session.currentClipId, error.getMessage());
            } catch (EOFException error) {
                session.trySendError(
                        session.currentClipId, "CHANNEL_ERROR unexpected_eof");
            } catch (Exception error) {
                session.trySendError(session.currentClipId, classify(error));
                Log.e(TAG, "Private TTS channel failed node=" + session.nodeId, error);
            } finally {
                handshakeHandler.removeCallbacks(handshakeTimeout);
                sessions.remove(session);
                session.close();
                if (claimed && active.compareAndSet(session, null)) {
                    if (terminal || !session.obsolete.get()) {
                        requestServiceStop(app, terminal
                                ? "phone ended private TTS stream"
                                : "private TTS channel failed");
                    }
                } else if (!claimed && !session.obsolete.get() && active.get() == null) {
                    requestServiceStop(app, "private TTS handshake failed");
                }
            }
        }

        private synchronized boolean claim(Session candidate, long generation) {
            if (closed.get() || generation <= activeGeneration.get()) return false;
            activeGeneration.set(generation);
            Session previous = active.getAndSet(candidate);
            if (previous != null && previous != candidate) previous.closeAsObsolete();
            return true;
        }

        private boolean readRecords(Session session, RoutePlayback playback) throws Exception {
            PrivateTtsProtocol.PhoneStreamValidator validator =
                    new PrivateTtsProtocol.PhoneStreamValidator();
            boolean clipActive = false;
            long clipId = 0L;
            int expectedSequence = 0;
            try {
                while (!closed.get() && !session.obsolete.get()) {
                    PrivateTtsProtocol.Record record =
                            PrivateTtsProtocol.readRecord(session.input);
                    validator.accept(record);
                    if (record == null) throw new EOFException("record stream ended");
                    switch (record.type) {
                        case PrivateTtsProtocol.TYPE_BEGIN:
                            if (clipActive || record.clipId <= 0L) {
                                throw new SinkException("PROTOCOL_ERROR nested_begin");
                            }
                            clipId = record.clipId;
                            session.currentClipId = clipId;
                            expectedSequence = 0;
                            playback.beginClip();
                            clipActive = true;
                            Log.i(TAG, "Private TTS BEGIN generation=" + session.generation
                                    + " clip=" + clipId + " purpose=" + record.purpose);
                            break;
                        case PrivateTtsProtocol.TYPE_PCM:
                            if (!clipActive || record.clipId != clipId
                                    || record.sequence != expectedSequence) {
                                throw new SinkException("PROTOCOL_ERROR pcm_order");
                            }
                            playback.writePcm(record.pcm);
                            if (expectedSequence == 0) {
                                playback.awaitFirstRealAudio();
                                session.sendAck(
                                        PrivateTtsProtocol.ACK_FIRST_AUDIO,
                                        clipId,
                                        "");
                                Log.i(TAG, "Private TTS FIRST_AUDIO generation="
                                        + session.generation + " clip=" + clipId);
                            }
                            expectedSequence++;
                            break;
                        case PrivateTtsProtocol.TYPE_END:
                            if (!clipActive || record.clipId != clipId
                                    || record.sequence != expectedSequence) {
                                throw new SinkException("PROTOCOL_ERROR end_order");
                            }
                            playback.awaitDrained();
                            session.sendAck(
                                    PrivateTtsProtocol.ACK_DRAINED,
                                    clipId,
                                    "");
                            Log.i(TAG, "Private TTS DRAINED generation="
                                    + session.generation + " clip=" + clipId
                                    + " records=" + expectedSequence);
                            playback.parkBetweenClips();
                            clipActive = false;
                            clipId = 0L;
                            session.currentClipId = 0L;
                            expectedSequence = 0;
                            break;
                        case PrivateTtsProtocol.TYPE_ABORT:
                            if (!clipActive || record.clipId != clipId) {
                                throw new SinkException("PROTOCOL_ERROR abort_order");
                            }
                            playback.abortClip();
                            clipActive = false;
                            clipId = 0L;
                            session.currentClipId = 0L;
                            expectedSequence = 0;
                            break;
                        case PrivateTtsProtocol.TYPE_STREAM_END:
                            if (clipActive) {
                                throw new SinkException("PROTOCOL_ERROR stream_end_during_clip");
                            }
                            return true;
                        default:
                            throw new SinkException("PROTOCOL_ERROR record_type");
                    }
                }
                return false;
            } finally {
                if (clipActive) playback.abortClip();
                session.currentClipId = 0L;
            }
        }
    }

    private static final class Session {
        final Context app;
        final ChannelClient.Channel channel;
        final String nodeId;
        final AtomicBoolean obsolete = new AtomicBoolean(false);
        final AtomicBoolean ready = new AtomicBoolean(false);
        final AtomicBoolean ackHeaderWritten = new AtomicBoolean(false);
        final AtomicBoolean resourcesClosed = new AtomicBoolean(false);
        volatile long generation = Long.MIN_VALUE;
        volatile long currentClipId;
        volatile AudioDeviceInfo expectedDevice;
        volatile InputStream rawInput;
        volatile OutputStream rawOutput;
        volatile DataInputStream input;
        volatile DataOutputStream output;

        Session(Context app, ChannelClient.Channel channel) {
            this.app = app;
            this.channel = channel;
            this.nodeId = clean(channel.getNodeId());
        }

        void openStreams() throws Exception {
            rawInput = Tasks.await(
                    Wearable.getChannelClient(app).getInputStream(channel),
                    15,
                    TimeUnit.SECONDS);
            rawOutput = Tasks.await(
                    Wearable.getChannelClient(app).getOutputStream(channel),
                    15,
                    TimeUnit.SECONDS);
            if (rawInput == null || rawOutput == null) {
                throw new IOException("channel stream unavailable");
            }
            input = new DataInputStream(new BufferedInputStream(rawInput, 32_768));
            output = new DataOutputStream(new BufferedOutputStream(rawOutput, 4_096));
        }

        synchronized void sendAck(int type, long clipId, String detail) throws IOException {
            if (obsolete.get() || !ackHeaderWritten.get() || output == null) return;
            PrivateTtsProtocol.writeAck(
                    output,
                    type,
                    clipId,
                    SystemClock.elapsedRealtimeNanos(),
                    detail == null ? "" : detail);
            output.flush();
        }

        void sendError(long clipId, String detail) throws IOException {
            sendAck(PrivateTtsProtocol.ACK_ERROR, clipId, detail);
        }

        void trySendError(long clipId, String detail) {
            if (obsolete.get()) return;
            try {
                sendError(clipId, normalizeError(detail));
            } catch (Exception ignored) {
                // The transport itself may already be gone.
            }
        }

        void closeAsObsolete() {
            obsolete.set(true);
            close();
        }

        void close() {
            if (!resourcesClosed.compareAndSet(false, true)) return;
            closeQuietly(input);
            closeQuietly(output);
            closeQuietly(rawInput);
            closeQuietly(rawOutput);
            PrivateTtsChannelService.closeChannel(app, channel);
        }
    }

    /** One channel-scoped AudioTrack, paused and focus-free between finite clips. */
    private static final class RoutePlayback implements AutoCloseable {
        private static final int SAMPLE_RATE = 24_000;
        private static final int PROBE_BYTES = 960;
        private static final long ROUTE_TIMEOUT_MS = 2_000L;
        private static final long FIRST_AUDIO_TIMEOUT_MS = 2_000L;

        private final Context app;
        private final AudioDeviceInfo expectedDevice;
        private final AtomicBoolean routeProven = new AtomicBoolean(false);
        private final AtomicBoolean routeChanged = new AtomicBoolean(false);
        private final Handler routeHandler = new Handler(Looper.getMainLooper());
        private final AudioRouting.OnRoutingChangedListener routingListener;

        private AudioManager audioManager;
        private AudioFocusRequest focusRequest;
        private AudioTrack track;
        private boolean focusHeld;
        private boolean clipActive;
        private long writtenFrames;
        private long firstRealFrame = -1L;

        RoutePlayback(Context app, AudioDeviceInfo expectedDevice) {
            this.app = app;
            this.expectedDevice = expectedDevice;
            this.routingListener = routing -> {
                if (routeProven.get()
                        && !PrivateTtsSoundFormRoute.exactMatch(
                                this.expectedDevice, routing.getRoutedDevice())) {
                    routeChanged.set(true);
                }
            };
        }

        void openAndProve() throws SinkException {
            int minBuffer = AudioTrack.getMinBufferSize(
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT);
            if (minBuffer <= 0) throw new SinkException("PLAYBACK_ERROR buffer");
            AudioAttributes attributes = new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build();
            audioManager = app.getSystemService(AudioManager.class);
            if (audioManager == null) throw new SinkException("PLAYBACK_ERROR audio_manager");
            focusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                    .setAudioAttributes(attributes)
                    .setAcceptsDelayedFocusGain(false)
                    .setOnAudioFocusChangeListener(
                            change -> { }, new Handler(Looper.getMainLooper()))
                    .build();

            track = new AudioTrack.Builder()
                    .setAudioAttributes(attributes)
                    .setAudioFormat(new AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(SAMPLE_RATE)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build())
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setBufferSizeInBytes(Math.max(minBuffer * 2, 48_000))
                    .build();
            if (track.getState() != AudioTrack.STATE_INITIALIZED) {
                throw new SinkException("PLAYBACK_ERROR track_init");
            }
            if (!track.setPreferredDevice(expectedDevice)) {
                throw new SinkException("ROUTE_REJECTED preferred_device");
            }
            track.addOnRoutingChangedListener(routingListener, routeHandler);
            startAndProveRoute();
        }

        void beginClip() throws SinkException {
            if (clipActive) throw new SinkException("PROTOCOL_ERROR nested_begin");
            startAndProveRoute();
            clipActive = true;
        }

        private void startAndProveRoute() throws SinkException {
            if (track == null) throw new SinkException("PLAYBACK_ERROR track_missing");
            routeProven.set(false);
            routeChanged.set(false);
            firstRealFrame = -1L;
            writtenFrames = presentedFrames();
            requestFocus();
            track.play();
            byte[] silence = new byte[PROBE_BYTES];
            writeExact(silence);

            long deadline = SystemClock.elapsedRealtime() + ROUTE_TIMEOUT_MS;
            while (SystemClock.elapsedRealtime() < deadline) {
                AudioDeviceInfo routed = track.getRoutedDevice();
                if (PrivateTtsSoundFormRoute.exactMatch(expectedDevice, routed)) {
                    routeProven.set(true);
                    ensureExactRoute();
                    Log.i(TAG, "Private TTS route proven "
                            + PrivateTtsSoundFormRoute.describe(routed));
                    return;
                }
                SystemClock.sleep(10L);
            }
            throw new SinkException("ROUTE_REJECTED actual="
                    + PrivateTtsSoundFormRoute.describe(track.getRoutedDevice()));
        }

        void parkBetweenClips() {
            clipActive = false;
            routeProven.set(false);
            if (track != null) {
                try {
                    track.pause();
                    track.flush();
                } catch (RuntimeException ignored) {
                    // The route may have disappeared concurrently with drain.
                }
            }
            abandonFocus();
        }

        void abortClip() {
            parkBetweenClips();
        }

        void writePcm(byte[] pcm) throws SinkException {
            if (!clipActive) throw new SinkException("PROTOCOL_ERROR pcm_without_begin");
            if (pcm == null || pcm.length < 2 || (pcm.length & 1) != 0) {
                throw new SinkException("PROTOCOL_ERROR pcm_size");
            }
            ensureExactRoute();
            if (firstRealFrame < 0L) firstRealFrame = writtenFrames;
            writeExact(pcm);
            ensureExactRoute();
        }

        void awaitFirstRealAudio() throws SinkException {
            if (firstRealFrame < 0L) throw new SinkException("PLAYBACK_ERROR no_pcm");
            long deadline = SystemClock.elapsedRealtime() + FIRST_AUDIO_TIMEOUT_MS;
            while (SystemClock.elapsedRealtime() < deadline) {
                ensureExactRoute();
                if (presentedFrames() > firstRealFrame) return;
                SystemClock.sleep(5L);
            }
            throw new SinkException("PLAYBACK_ERROR first_audio_timeout");
        }

        void awaitDrained() throws SinkException {
            long remainingFrames = Math.max(0L, writtenFrames - presentedFrames());
            long expectedMs = (remainingFrames * 1_000L) / SAMPLE_RATE;
            long timeoutMs = Math.min(30_000L, Math.max(2_000L, expectedMs + 2_000L));
            long deadline = SystemClock.elapsedRealtime() + timeoutMs;
            while (SystemClock.elapsedRealtime() < deadline) {
                ensureExactRoute();
                if (presentedFrames() >= writtenFrames) return;
                SystemClock.sleep(5L);
            }
            throw new SinkException("PLAYBACK_ERROR drain_timeout");
        }

        private void writeExact(byte[] bytes) throws SinkException {
            int offset = 0;
            while (offset < bytes.length) {
                ensureNotChangedAfterProof();
                // Keep the unavoidable route-change race bounded to one 20 ms
                // packet rather than leaking a whole 32 KiB phone record.
                int count = Math.min(PROBE_BYTES, bytes.length - offset);
                int written = track.write(bytes, offset, count, AudioTrack.WRITE_BLOCKING);
                if (written <= 0) {
                    throw new SinkException("PLAYBACK_ERROR write=" + written);
                }
                offset += written;
                writtenFrames += written / 2L;
            }
        }

        private long presentedFrames() {
            if (track == null) return 0L;
            // PlaybackHeadPosition counts frames that have actually played.
            // AudioTimestamp can include frames merely committed for future
            // presentation and can remain stale across pause/flush.
            return Integer.toUnsignedLong(track.getPlaybackHeadPosition());
        }

        private void ensureNotChangedAfterProof() throws SinkException {
            if (routeProven.get()) ensureExactRoute();
        }

        private void ensureExactRoute() throws SinkException {
            if (routeChanged.get()
                    || track == null
                    || !PrivateTtsSoundFormRoute.exactMatch(
                            expectedDevice, track.getRoutedDevice())) {
                throw new SinkException("ROUTE_CHANGED actual="
                        + PrivateTtsSoundFormRoute.describe(
                                track == null ? null : track.getRoutedDevice()));
            }
        }

        private void requestFocus() throws SinkException {
            if (focusHeld) return;
            if (audioManager.requestAudioFocus(focusRequest)
                    != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
                throw new SinkException("PLAYBACK_ERROR audio_focus");
            }
            focusHeld = true;
        }

        private void abandonFocus() {
            if (!focusHeld || audioManager == null || focusRequest == null) return;
            audioManager.abandonAudioFocusRequest(focusRequest);
            focusHeld = false;
        }

        @Override
        public void close() {
            routeProven.set(false);
            clipActive = false;
            AudioTrack current = track;
            track = null;
            if (current != null) {
                try {
                    current.removeOnRoutingChangedListener(routingListener);
                } catch (RuntimeException ignored) {
                    // Best-effort listener cleanup.
                }
                try {
                    current.pause();
                    current.flush();
                    current.stop();
                } catch (RuntimeException ignored) {
                    // A route failure may already have stopped the track.
                }
                current.release();
            }
            abandonFocus();
            focusRequest = null;
            audioManager = null;
        }
    }

    private static final class SinkException extends Exception {
        SinkException(String message) {
            super(normalizeError(message));
        }
    }

    private static void requestServiceStop(Context context, String reason) {
        Log.w(TAG, "Private TTS terminal: " + reason);
        stop(context);
    }

    private static String classify(Exception error) {
        if (error instanceof SecurityException) {
            return "ROUTE_REJECTED bluetooth_permission";
        }
        if (error instanceof IOException) return "CHANNEL_ERROR " + safe(error);
        return "PLAYBACK_ERROR " + safe(error);
    }

    private static String normalizeError(String detail) {
        String clean = detail == null ? "" : detail.trim().replaceAll("\\s+", " ");
        if (clean.isEmpty()) return "PLAYBACK_ERROR unknown";
        String[] allowed = {
                "NO_SOUNDFORM", "AMBIGUOUS_SOUNDFORM", "ROUTE_REJECTED",
                "ROUTE_CHANGED", "PROTOCOL_ERROR", "PLAYBACK_ERROR",
                "CHANNEL_ERROR", "OBSOLETE_STREAM", "SERVICE_NOT_ARMED"
        };
        for (String prefix : allowed) {
            if (clean.equals(prefix) || clean.startsWith(prefix + " ")) {
                return truncateUtf8(clean, PrivateTtsProtocol.MAX_ACK_DETAIL_BYTES);
            }
        }
        return truncateUtf8(
                "PLAYBACK_ERROR " + clean,
                PrivateTtsProtocol.MAX_ACK_DETAIL_BYTES);
    }

    private static String truncateUtf8(String value, int maxBytes) {
        String clean = value == null ? "" : value;
        if (clean.getBytes(StandardCharsets.UTF_8).length <= maxBytes) return clean;
        int end = clean.length();
        while (end > 0) {
            if (end < clean.length()
                    && Character.isLowSurrogate(clean.charAt(end))) {
                end--;
            }
            String candidate = clean.substring(0, end);
            if (candidate.getBytes(StandardCharsets.UTF_8).length <= maxBytes) {
                return candidate;
            }
            end--;
        }
        return "PLAYBACK_ERROR";
    }

    private static String safe(Throwable error) {
        if (error == null) return "unknown";
        String message = error.getMessage();
        if (message == null || message.trim().isEmpty()) {
            return error.getClass().getSimpleName();
        }
        return message.trim().replaceAll("\\s+", " ");
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static void closeQuietly(AutoCloseable closeable) {
        if (closeable == null) return;
        try {
            closeable.close();
        } catch (Exception ignored) {
            // Best effort during a terminal channel path.
        }
    }
}
