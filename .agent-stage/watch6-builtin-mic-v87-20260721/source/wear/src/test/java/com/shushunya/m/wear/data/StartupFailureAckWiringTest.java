package com.shushunya.m.wear.data;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.Test;

public final class StartupFailureAckWiringTest {
    @Test
    public void durableOutboxIgnoresMessageClientTaskSuccessAndWaitsForAck() throws Exception {
        String outbox = read("data/WatchStartupFailureOutbox.java");
        assertTrue(outbox.contains("createDeviceProtectedStorageContext()"));
        assertTrue(outbox.contains("PATH_STARTUP_FAILURE"));
        assertTrue(outbox.contains("only acknowledge() may clear"));
        assertFalse(outbox.contains("task.isSuccessful()"));
        assertFalse(outbox.contains("addOnCompleteListener"));
    }

    @Test
    public void listenerRoutesExactAckIntoOutbox() throws Exception {
        String listener = read("data/WearStateListenerService.java");
        assertTrue(listener.contains("PATH_STARTUP_FAILURE_ACK"));
        assertTrue(listener.contains("WatchStartupFailureOutbox.acknowledge("));
        assertTrue(listener.contains("event.getSourceNodeId()"));
    }

    private static String read(String suffix) throws Exception {
        return new String(Files.readAllBytes(Path.of(
                "src/main/java/com/shushunya/m/wear/" + suffix)), StandardCharsets.UTF_8);
    }
}
