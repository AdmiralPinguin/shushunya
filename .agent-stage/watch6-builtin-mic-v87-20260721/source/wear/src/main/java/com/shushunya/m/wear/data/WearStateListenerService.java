package com.shushunya.m.wear.data;

import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import com.google.android.gms.wearable.MessageEvent;
import com.google.android.gms.wearable.WearableListenerService;
import com.shushunya.m.wear.audio.WearAudioLifecycleProtocol;
import com.shushunya.m.wear.audio.WearMicForegroundService;
import com.shushunya.m.wear.control.DurableMagicWakeCoordinator;
import com.shushunya.m.wear.control.Haptics;
import com.shushunya.m.wear.control.PowerConfMode;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.util.Locale;

public final class WearStateListenerService extends WearableListenerService {
    private static final String TAG = "ShushunyaWearControl";

    @Override
    public void onMessageReceived(MessageEvent messageEvent) {
        PowerConfMode.enforce(this);
        WatchStartupFailureOutbox.ensureDelivery(this);
        if (WearAudioLifecycleProtocol.PATH_STARTUP_FAILURE_ACK.equals(
                messageEvent.getPath())) {
            handleStartupFailureAck(messageEvent);
            return;
        }
        if (WearAudioLifecycleProtocol.PATH_BINDING.equals(messageEvent.getPath())) {
            handleAudioBinding(messageEvent);
            return;
        }
        if (WearAudioLifecycleProtocol.PATH_DRAIN.equals(messageEvent.getPath())) {
            handleAudioDrain(messageEvent);
            return;
        }
        if (WearAudioLifecycleProtocol.PATH_TERMINAL_ACK.equals(messageEvent.getPath())) {
            handleAudioTerminalAck(messageEvent);
            return;
        }
        if (WearProtocol.PATH_MAGIC_ACCEPTED.equals(messageEvent.getPath())) {
            handleMagicAccepted(messageEvent);
            return;
        }
        if (WearProtocol.PATH_MAGIC_PREPARED.equals(messageEvent.getPath())) {
            handleMagicPrepared(messageEvent);
            return;
        }
        if (!WearProtocol.PATH_STATE.equals(messageEvent.getPath())) return;
        if (!ControlPhoneTargetStore.acceptOrRemember(
                this, messageEvent.getSourceNodeId())) {
            Log.w(TAG, "Ignoring state from non-selected phone node="
                    + messageEvent.getSourceNodeId());
            return;
        }
        try {
            JSONObject root = new JSONObject(
                    new String(messageEvent.getData(), StandardCharsets.UTF_8));
            Object requestField = root.opt("requestId");
            String requestId = requestField instanceof String
                    ? (String) requestField
                    : root.optString("requestId", "");
            Object errorField = root.opt("error");
            boolean exactErrorField = root.has("error")
                    && (root.isNull("error") || errorField instanceof String);
            String error = root.isNull("error") ? "" : root.optString("error", "");
            boolean hasError = !error.trim().isEmpty();
            boolean matchingLiveCommand = ControllerStateStore.isMatchingPending(
                    this, ControllerStateStore.Kind.LIVE, requestId);
            boolean matchingMusicCommand = ControllerStateStore.isMatchingPending(
                    this, ControllerStateStore.Kind.MUSIC, requestId);
            // Capture the immutable SET direction before updateLive consumes the
            // exact pending UUID and before the durable coordinator is cleared.
            // A background/empty-id observation intentionally has no direction.
            Boolean pendingTargetStart = matchingLiveCommand
                    ? DurableMagicWakeCoordinator.pendingTargetStartExact(this, requestId)
                    : null;
            boolean liveChanged = false;
            boolean musicChanged = false;
            boolean completeStandaloneMusic = false;
            String terminalMusicState = "";

            JSONObject live = root.optJSONObject("live");
            if (live != null) {
                // Read correlation before updateLive clears the matching pending id.
                boolean liveApplied = ControllerStateStore.updateLive(
                        this,
                        requestId,
                        live.optString("state", ""),
                        live.optString("status", ""),
                        live.optBoolean("armed", false),
                        live.optString("selectedMic", ""),
                        live.optString("actualMic", ""));
                liveChanged = liveApplied;
                String liveState = live.optString("state", "")
                        .trim().toLowerCase(Locale.ROOT);
                JSONObject magic = root.optJSONObject("magic");
                boolean exactStarted = MagicSettlePolicy.isExactStartedState(
                        matchingLiveCommand,
                        liveApplied,
                        hasError,
                        liveState,
                        magic != null,
                        magic != null && magic.optBoolean("engaged", false));
                boolean exactTerminal = liveApplied
                        && matchingLiveCommand
                        && !ControllerStateStore.isMatchingPending(
                                this, ControllerStateStore.Kind.LIVE, requestId);
                if (exactTerminal) {
                    boolean phoneLive = "running".equals(liveState)
                            && magic != null
                            && magic.optBoolean("engaged", false);
                    if (WatchMicActivationPolicy.captureAction(
                            phoneLive,
                            true,
                            true,
                            pendingTargetStart != null,
                            Boolean.TRUE.equals(pendingTargetStart),
                            hasError) == WatchMicActivationPolicy.CaptureAction.STOP_CAPTURE) {
                        WearMicForegroundService.stop(this);
                    }
                    // updateLive has atomically consumed this exact terminal
                    // UUID. Only now may the durable wake coordinator clear.
                    DurableMagicWakeCoordinator.completeExactTerminal(
                            this, requestId, hasError, exactStarted);
                }
                if (liveApplied) {
                    if (exactStarted && ControllerStateStore.beginMagicSettle(
                            this, requestId, System.currentTimeMillis())) {
                        Haptics.strongSuccess(this);
                    }
                }
            }

            JSONObject music = root.optJSONObject("music");
            if (music != null) {
                Object musicStateField = music.opt("state");
                terminalMusicState = musicStateField instanceof String
                        ? (String) musicStateField
                        : music.optString("state", "");
                boolean standaloneMusic = !requestId.isEmpty()
                        && requestId.equals(
                        MusicCommandCoordinator.pendingRequestId(this));
                boolean exactStandaloneAck = !standaloneMusic
                        || MusicCommandCoordinator.acceptsExactSemanticAck(
                        this,
                        messageEvent.getSourceNodeId(),
                        requestId,
                        terminalMusicState,
                        exactErrorField);
                if (exactStandaloneAck) {
                    musicChanged = ControllerStateStore.updateMusic(
                            this,
                            requestId,
                            terminalMusicState,
                            music.optString("packageName", ""),
                            music.optString("title", ""));
                    completeStandaloneMusic = standaloneMusic && musicChanged;
                } else {
                    Log.w(TAG, "Ignoring non-semantic or wrong-peer MUSIC ACK");
                }
            }

            if (hasError && matchingLiveCommand) {
                if (live == null) {
                    boolean exactTerminal = ControllerStateStore.updateCommandError(
                            this, ControllerStateStore.Kind.LIVE, requestId, error);
                    if (exactTerminal) {
                        if (WatchMicActivationPolicy.captureAction(
                                false,
                                true,
                                true,
                                pendingTargetStart != null,
                                Boolean.TRUE.equals(pendingTargetStart),
                                true) == WatchMicActivationPolicy.CaptureAction.STOP_CAPTURE) {
                            WearMicForegroundService.stop(this);
                        }
                        DurableMagicWakeCoordinator.completeExactTerminal(
                                this, requestId, true, false);
                        liveChanged = true;
                    }
                } else {
                    // updateLive deliberately preserves the final state snapshot;
                    // attach the exact command diagnostic without changing capture
                    // ownership a second time.
                    ControllerStateStore.recordCommandError(
                            this, ControllerStateStore.Kind.LIVE, error);
                    liveChanged = true;
                }
            }
            if (hasError && matchingMusicCommand && !matchingLiveCommand) {
                ControllerStateStore.recordCommandError(
                        this, ControllerStateStore.Kind.MUSIC, error);
                musicChanged = true;
            }
            if (completeStandaloneMusic) {
                MusicCommandCoordinator.completeExactAfterState(
                        this,
                        messageEvent.getSourceNodeId(),
                        requestId,
                        terminalMusicState,
                        exactErrorField,
                        hasError);
            }
            if (liveChanged) scheduleLiveRefresh();
            if (musicChanged) {
                ComplicationRefresh.request(this, ControllerStateStore.Kind.MUSIC);
            }
        } catch (Exception error) {
            // A malformed phone response must not crash a manifest-started listener.
            Log.w(TAG, "Ignoring malformed phone state", error);
        }
    }

    private void handleAudioBinding(MessageEvent event) {
        try {
            JSONObject root = boundedJson(event);
            Long version = exactLong(root, "version");
            Long runGeneration = exactLong(root, "runGeneration");
            Long sessionId = exactLong(root, "sessionId");
            String requestId = exactString(root, "requestId");
            String captureGroupId = exactString(root, "captureGroupId");
            if (version == null || version != WearAudioLifecycleProtocol.VERSION
                    || runGeneration == null || sessionId == null
                    || requestId == null || captureGroupId == null) return;
            boolean accepted = WearMicForegroundService.acceptPhoneBinding(
                    event.getSourceNodeId(),
                    requestId,
                    captureGroupId,
                    runGeneration,
                    sessionId);
            if (!accepted) Log.w(TAG, "Rejected stale/invalid Watch PCM binding");
        } catch (Exception error) {
            Log.w(TAG, "Ignoring malformed Watch PCM binding", error);
        }
    }

    private void handleAudioDrain(MessageEvent event) {
        try {
            JSONObject root = boundedJson(event);
            Long version = exactLong(root, "version");
            Long runGeneration = exactLong(root, "runGeneration");
            Long sessionId = exactLong(root, "sessionId");
            Long timeoutMs = exactLong(root, "timeoutMs");
            String requestId = exactString(root, "requestId");
            String captureGroupId = exactString(root, "captureGroupId");
            if (version == null || version != WearAudioLifecycleProtocol.VERSION
                    || runGeneration == null || sessionId == null || timeoutMs == null
                    || requestId == null || captureGroupId == null) return;
            boolean accepted = WearMicForegroundService.requestPhoneDrain(
                    event.getSourceNodeId(),
                    requestId,
                    captureGroupId,
                    runGeneration,
                    sessionId,
                    timeoutMs);
            if (!accepted) Log.w(TAG, "Rejected stale/invalid Watch PCM drain");
        } catch (Exception error) {
            Log.w(TAG, "Ignoring malformed Watch PCM drain", error);
        }
    }

    private void handleAudioTerminalAck(MessageEvent event) {
        try {
            JSONObject root = boundedJson(event);
            Long version = exactLong(root, "version");
            Long runGeneration = exactLong(root, "runGeneration");
            Long sessionId = exactLong(root, "sessionId");
            Long acceptedLastSequence = exactLong(root, "acceptedLastSequence");
            Long ackAtMs = exactLong(root, "ackAtMs");
            String requestId = exactString(root, "requestId");
            String captureGroupId = exactString(root, "captureGroupId");
            String disposition = exactString(root, "disposition");
            String errorDetail = exactString(root, "error");
            if (version == null || version != WearAudioLifecycleProtocol.VERSION
                    || runGeneration == null || sessionId == null
                    || acceptedLastSequence == null || ackAtMs == null
                    || requestId == null || captureGroupId == null
                    || disposition == null || errorDetail == null) return;
            if (errorDetail.length() > 2_048) return;
            boolean accepted = WearMicForegroundService.acceptTerminalAck(
                    event.getSourceNodeId(),
                    requestId,
                    captureGroupId,
                    runGeneration,
                    sessionId,
                    disposition,
                    acceptedLastSequence,
                    errorDetail,
                    ackAtMs);
            if (!accepted) Log.w(TAG, "Rejected stale/invalid Watch PCM terminal ACK");
        } catch (Exception error) {
            Log.w(TAG, "Ignoring malformed Watch PCM terminal ACK", error);
        }
    }

    private void handleStartupFailureAck(MessageEvent event) {
        try {
            JSONObject root = boundedJson(event);
            Long version = exactLong(root, "version");
            Long failedAtMs = exactLong(root, "failedAtMs");
            Long ackAtMs = exactLong(root, "ackAtMs");
            String requestId = exactString(root, "requestId");
            String code = exactString(root, "code");
            if (version == null || version != WearAudioLifecycleProtocol.VERSION
                    || failedAtMs == null || ackAtMs == null
                    || requestId == null || code == null
                    || ackAtMs <= 0L) return;
            boolean accepted = WatchStartupFailureOutbox.acknowledge(
                    this,
                    event.getSourceNodeId(),
                    requestId,
                    code,
                    failedAtMs);
            if (!accepted) Log.w(TAG, "Rejected stale/invalid startup failure ACK");
        } catch (Exception error) {
            Log.w(TAG, "Ignoring malformed startup failure ACK", error);
        }
    }

    private static JSONObject boundedJson(MessageEvent event) throws Exception {
        byte[] data = event == null ? null : event.getData();
        if (data == null || data.length == 0
                || data.length > WearAudioLifecycleProtocol.MAX_MESSAGE_BYTES) return null;
        String json = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(data))
                .toString();
        return new JSONObject(json);
    }

    /** Rejects JSON strings, booleans, floating point and exponent coercion. */
    private static Long exactLong(JSONObject root, String key) {
        if (root == null || key == null || !root.has(key)) return null;
        Object value = root.opt(key);
        if (!(value instanceof Byte)
                && !(value instanceof Short)
                && !(value instanceof Integer)
                && !(value instanceof Long)) return null;
        return ((Number) value).longValue();
    }

    private static String exactString(JSONObject root, String key) {
        if (root == null || key == null || !root.has(key)) return null;
        Object value = root.opt(key);
        return value instanceof String ? (String) value : null;
    }

    private void handleMagicAccepted(MessageEvent messageEvent) {
        try {
            byte[] data = messageEvent.getData();
            if (data == null || data.length == 0 || data.length > 16_384) return;
            JSONObject root = new JSONObject(new String(data, StandardCharsets.UTF_8));
            String requestId = MagicAcceptedPolicy.cleanRequestId(
                    root.optString("requestId", ""));
            String sourceNodeId = MagicAcceptedPolicy.cleanNodeId(
                    messageEvent.getSourceNodeId());
            if (requestId.isEmpty() || sourceNodeId.isEmpty()
                    || !root.has("targetStart")) return;
            boolean targetStart = root.optBoolean("targetStart", false);
            if (!ControlPhoneTargetStore.acceptsExistingOrEmpty(this, sourceNodeId)) {
                Log.w(TAG, "Ignoring ACCEPTED from non-selected phone node=" + sourceNodeId);
                return;
            }

            // Data Layer already authenticates the paired package/signature.
            // The still-running coordinator consumes this only when both the
            // UUID and the exact phone node selected for RemoteActivity match.
            MagicAcceptedRegistry.record(
                    requestId,
                    sourceNodeId,
                    targetStart,
                    SystemClock.elapsedRealtime());
            if (DurableMagicWakeCoordinator.markAcceptedExact(
                    this, requestId, sourceNodeId, targetStart)) {
                ControlPhoneTargetStore.rememberExact(this, sourceNodeId);
            }
            Log.i(TAG, "Phone bridge accepted magic request=" + requestId
                    + " source=" + sourceNodeId
                    + " targetStart=" + targetStart);
        } catch (Exception error) {
            Log.w(TAG, "Ignoring malformed ACCEPTED acknowledgement", error);
        }
    }

    private void handleMagicPrepared(MessageEvent messageEvent) {
        try {
            byte[] data = messageEvent.getData();
            if (data == null || data.length == 0 || data.length > 16_384) return;
            JSONObject root = new JSONObject(new String(data, StandardCharsets.UTF_8));
            String requestId = PreparedAckPolicy.cleanRequestId(
                    root.optString("requestId", ""));
            String sourceNodeId = PreparedAckPolicy.cleanNodeId(
                    messageEvent.getSourceNodeId());
            if (requestId.isEmpty() || sourceNodeId.isEmpty()) return;

            // Do not cache an ACK for a request that is no longer the exact
            // pending magic command. Source validation below is independent:
            // both correlation and nearby-node identity must hold.
            if (!ControllerStateStore.isMatchingPending(
                            this, ControllerStateStore.Kind.LIVE, requestId)
                    || !ControllerStateStore.isMatchingPending(
                            this, ControllerStateStore.Kind.MUSIC, requestId)) {
                Log.w(TAG, "Ignoring stale/wrong PREPARED request=" + requestId);
                return;
            }

            // Do not perform another cold NodeClient topology lookup here.
            // The coordinator already has the exact nodes whose PREPARE send
            // task succeeded and consumes this inbox only for that exact source.
            PreparedAckRegistry.record(
                    requestId,
                    sourceNodeId,
                    SystemClock.elapsedRealtime());
            Log.i(TAG, "Phone durably prepared magic request=" + requestId
                    + " source=" + sourceNodeId);
        } catch (Exception error) {
            Log.w(TAG, "Ignoring malformed PREPARED acknowledgement", error);
        }
    }

    private void scheduleLiveRefresh() {
        ControllerStateStore.Snapshot snapshot = ControllerStateStore.snapshot(
                this, ControllerStateStore.Kind.LIVE);
        if (snapshot.livePhase == ControllerStateStore.LivePhase.NONE) {
            ComplicationRefresh.request(this, ControllerStateStore.Kind.LIVE);
            return;
        }

        // The tap already published STARTING/STOPPING. Do not re-publish the same
        // token on successful transport/state callbacks because that can restart
        // the watch-face animation.
        long delay = ControllerStateStore.livePhaseClearDelay(this);
        if (delay < 0L) return;
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            if (ControllerStateStore.clearLivePhaseIfDue(this)) {
                ComplicationRefresh.request(this, ControllerStateStore.Kind.LIVE);
            }
        }, Math.max(1L, delay));
    }
}
