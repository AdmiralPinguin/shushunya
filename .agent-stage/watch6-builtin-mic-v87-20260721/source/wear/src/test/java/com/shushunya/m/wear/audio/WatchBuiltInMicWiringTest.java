package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.Test;

public final class WatchBuiltInMicWiringTest {
    @Test
    public void productionCaptureIsStrictlyWatchBuiltInAndKeepsStartupProof() throws Exception {
        String source = new String(
                Files.readAllBytes(Path.of(
                        "src/main/java/com/shushunya/m/wear/audio/WearMicForegroundService.java")),
                StandardCharsets.UTF_8);

        int proof = source.indexOf("openBuiltInCaptureWithStartupProof(");
        int channel = source.indexOf("openFramedChannel(", proof);
        assertTrue(proof >= 0);
        assertTrue(channel > proof);
        assertTrue(source.contains("MediaRecorder.AudioSource.VOICE_RECOGNITION"));
        assertTrue(source.contains("AudioDeviceInfo.TYPE_BUILTIN_MIC"));
        assertTrue(source.contains("record.setPreferredDevice(builtInInput)"));
        assertTrue(source.contains("record.getSampleRate() != WearAudioProtocol.SAMPLE_RATE"));
        assertTrue(source.contains("record.getChannelCount() != 1"));
        assertTrue(source.contains("ZeroPcmStartupGuard.Decision.REACQUIRE"));
        assertTrue(source.contains("WATCH_BUILTIN_ZERO_PCM"));
        assertTrue(source.contains("activeRecord.release()"));
        assertTrue(source.contains("stopBeforeForegroundRequested = true"));
        assertTrue(source.contains("consumeStopRequestedBeforeForeground()"));

        assertFalse(source.contains("Manifest.permission.BLUETOOTH_CONNECT"));
        assertFalse(source.contains("TYPE_BLUETOOTH_SCO"));
        assertFalse(source.contains("BluetoothPowerConfVoiceSession"));
        assertFalse(source.contains("PowerConfScoSessionPolicy"));
        assertFalse(source.contains("MediaRecorder.AudioSource.VOICE_COMMUNICATION"));
        assertFalse(source.contains("MediaRecorder.AudioSource.MIC"));
    }

    @Test
    public void visibleLaunchRequiresMicrophoneButNotBluetoothPermission() throws Exception {
        String source = new String(
                Files.readAllBytes(Path.of(
                        "src/main/java/com/shushunya/m/wear/control/MagicToggleActivity.java")),
                StandardCharsets.UTF_8);
        assertTrue(source.contains("Manifest.permission.RECORD_AUDIO"));
        assertFalse(source.contains("Manifest.permission.BLUETOOTH_CONNECT"));
        assertTrue(source.contains("startWatchMicUplinkExact("));
        assertTrue(source.contains("WearMicForegroundService.startExact(this, request, node)"));
        assertFalse(source.contains("startForegroundService(new Intent(this, WearMicForegroundService.class)"));
    }
}
