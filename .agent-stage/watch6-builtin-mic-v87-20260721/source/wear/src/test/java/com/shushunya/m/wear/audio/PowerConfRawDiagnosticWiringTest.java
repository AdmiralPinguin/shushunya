package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.Test;

public final class PowerConfRawDiagnosticWiringTest {
    @Test
    public void adbTrampolineIsDumpProtectedAndCaptureServiceIsPrivate() throws Exception {
        String manifest = read("src/main/AndroidManifest.xml");
        int activity = manifest.indexOf(".wear.audio.PowerConfRawDiagnosticActivity");
        int service = manifest.indexOf(".wear.audio.PowerConfRawDiagnosticService");
        assertTrue(activity >= 0);
        assertTrue(service > activity);
        assertTrue(manifest.substring(activity, service).contains(
                "android:permission=\"android.permission.DUMP\""));
        assertTrue(manifest.substring(service).contains("android:exported=\"false\""));
    }

    @Test
    public void diagnosticNeverOpensPhoneOrServerTransportAndPinsEverySource() throws Exception {
        String source = read(
                "src/main/java/com/shushunya/m/wear/audio/PowerConfRawDiagnosticService.java");
        assertTrue(source.contains("PowerConfScoSessionPolicy.EXPECTED_ADDRESS"));
        assertTrue(source.contains("BluetoothPowerConfVoiceSession.openRouteOnly("));
        assertTrue(source.contains("getAvailableCommunicationDevices()"));
        assertTrue(source.contains("legacy_start_voice_recognition_called\", false"));
        assertTrue(source.contains("isClientSilenced()"));
        assertTrue(source.contains("record.setPreferredDevice(owner.input)"));
        assertTrue(source.contains("TYPE_BLUETOOTH_SCO"));
        assertTrue(source.contains("VOICE_COMMUNICATION"));
        assertTrue(source.contains("VOICE_RECOGNITION"));
        assertTrue(source.contains("AudioSource.MIC"));
        assertTrue(source.contains("getExternalFilesDir(\"diagnostics\")"));
        assertFalse(source.contains("Wearable.getChannelClient"));
        assertFalse(source.contains("Wearable.getMessageClient"));
        assertFalse(source.contains("HttpURLConnection"));
        assertFalse(source.contains("Socket("));
    }

    private static String read(String relativePath) throws Exception {
        return new String(
                Files.readAllBytes(Path.of(relativePath)), StandardCharsets.UTF_8);
    }
}
