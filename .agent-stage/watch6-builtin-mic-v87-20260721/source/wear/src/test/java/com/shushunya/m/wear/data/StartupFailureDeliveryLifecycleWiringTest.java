package com.shushunya.m.wear.data;

import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.Test;

public final class StartupFailureDeliveryLifecycleWiringTest {
    @Test
    public void independentOsAnchorSurvivesCommandTerminalUntilExactAck() throws Exception {
        String outbox = read("src/main/java/com/shushunya/m/wear/data/"
                + "WatchStartupFailureOutbox.java");
        String anchor = read("src/main/java/com/shushunya/m/wear/data/"
                + "WatchStartupFailureDeliveryService.java");
        String manifest = read("src/main/AndroidManifest.xml");

        assertTrue(outbox.contains("ensureDelivery(Context context)"));
        assertTrue(outbox.contains("WatchStartupFailureDeliveryService.start(context)"));
        assertTrue(outbox.contains("WatchStartupFailureDeliveryService.stop(context)"));
        assertTrue(anchor.contains("HARD_DEADLINE_MS = 30_000L"));
        assertTrue(anchor.contains("return START_NOT_STICKY;"));
        assertTrue(anchor.contains("onTimeout(int startId, int fgsType)"));
        assertTrue(anchor.contains("WatchStartupFailureOutbox.cancelDeliveryWindow()"));
        assertTrue(outbox.contains("DELIVERY_WINDOW.get() != window"));
        assertTrue(anchor.contains("WatchStartupFailureOutbox.resume(this)"));
        assertTrue(manifest.contains(".wear.data.WatchStartupFailureDeliveryService"));
        assertTrue(anchor.contains("FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE"));
        assertTrue(manifest.contains("android:foregroundServiceType=\"connectedDevice\""));
        assertTrue(manifest.contains("android.permission.CHANGE_NETWORK_STATE"));
    }

    private static String read(String path) throws Exception {
        return new String(Files.readAllBytes(Path.of(path)), StandardCharsets.UTF_8);
    }
}
