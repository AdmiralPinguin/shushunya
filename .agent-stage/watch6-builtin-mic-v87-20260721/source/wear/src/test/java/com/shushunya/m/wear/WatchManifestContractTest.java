package com.shushunya.m.wear;

import static org.junit.Assert.assertTrue;
import static org.junit.Assert.assertFalse;

import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

public final class WatchManifestContractTest {
    @Test
    public void declaresAudioRoutingPermissionAndDormantPrivateTts() throws Exception {
        String manifest = new String(
                Files.readAllBytes(Path.of("src/main/AndroidManifest.xml")),
                StandardCharsets.UTF_8);
        assertTrue(manifest.contains("android.permission.MODIFY_AUDIO_SETTINGS"));
        assertTrue(manifest.contains(".wear.audio.PrivateTtsPlaybackService"));
        assertTrue(manifest.contains(".wear.audio.PrivateTtsChannelService"));
        assertTrue(manifest.split("android:enabled=\"false\"", -1).length >= 3);
    }

    @Test
    public void registersEveryPhoneOwnedWatchAudioLifecycleMessage() throws Exception {
        String manifest = read("src/main/AndroidManifest.xml");
        assertTrue(manifest.contains("/shushunya/audio/watch/binding/v1"));
        assertTrue(manifest.contains("/shushunya/audio/watch/drain/v1"));
        assertTrue(manifest.contains("/shushunya/audio/watch/terminal-ack/v1"));
        assertTrue(manifest.contains(
                "/shushunya/audio/watch/startup-failure-ack/v1"));
    }

    @Test
    public void notificationStopUsesVisibleMagicSetInsteadOfDirectServiceStop()
            throws Exception {
        String service = read(
                "src/main/java/com/shushunya/m/wear/audio/WearMicForegroundService.java");
        assertTrue(service.contains(
                "new Intent(this, MagicToggleActivity.class)"));
        assertTrue(service.contains(
                ".setAction(WearActionReceiver.ACTION_MAGIC_TOGGLE)"));
        String notificationBlock = service.substring(service.indexOf(
                "private Notification buildNotification"));
        assertFalse(notificationBlock.contains(
                ".setAction(WearMicForegroundService.ACTION_STOP)"));
    }

    @Test
    public void releaseIdentityIsStrictlyV87() throws Exception {
        String gradle = read("build.gradle");
        assertTrue(gradle.contains("versionCode = 87"));
        assertTrue(gradle.contains(
                "versionName = \"8.28-wear-watch6-builtin-mic-racefix-v87\""));
        assertFalse(gradle.contains("versionCode = 86"));
    }

    private static String read(String relativePath) throws Exception {
        return new String(
                Files.readAllBytes(Path.of(relativePath)),
                StandardCharsets.UTF_8);
    }
}
