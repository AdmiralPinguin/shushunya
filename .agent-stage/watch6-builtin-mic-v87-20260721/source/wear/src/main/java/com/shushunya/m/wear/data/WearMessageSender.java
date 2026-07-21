package com.shushunya.m.wear.data;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import com.google.android.gms.tasks.Task;
import com.google.android.gms.tasks.TaskCompletionSource;
import com.google.android.gms.tasks.Tasks;
import com.google.android.gms.wearable.Node;
import com.google.android.gms.wearable.Wearable;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public final class WearMessageSender {
    private WearMessageSender() {}

    public static Task<Boolean> sendToNearby(
            Context context,
            String path,
            String jsonPayload) {
        TaskCompletionSource<Boolean> result = new TaskCompletionSource<>();
        Wearable.getNodeClient(context.getApplicationContext())
                .getConnectedNodes()
                .addOnSuccessListener(nodes -> {
                    List<Task<Integer>> sends = new ArrayList<>();
                    byte[] payload = jsonPayload.getBytes(StandardCharsets.UTF_8);
                    for (Node node : nodes) {
                        if (node.isNearby()) {
                            sends.add(Wearable.getMessageClient(context.getApplicationContext())
                                    .sendMessage(node.getId(), path, payload));
                        }
                    }
                    if (sends.isEmpty()) {
                        result.setResult(false);
                        return;
                    }
                    Tasks.whenAllComplete(sends).addOnCompleteListener(ignored -> {
                        boolean anyQueued = false;
                        for (Task<Integer> send : sends) {
                            anyQueued |= send.isSuccessful();
                        }
                        result.setResult(anyQueued);
                    });
                })
                .addOnFailureListener(error -> result.setResult(false));
        return result.getTask();
    }

    /**
     * Queues a message to every nearby node and returns the exact nodes whose
     * transport task succeeded. Callers can then reject an ACK from any other
     * node, including one left over from an earlier or parallel dispatch.
     */
    public static Task<NearbySendResult> sendToNearbyTargets(
            Context context,
            String path,
            String jsonPayload) {
        TaskCompletionSource<NearbySendResult> result = new TaskCompletionSource<>();
        Context app = context.getApplicationContext();
        attemptNearbyTargets(
                app,
                path,
                jsonPayload.getBytes(StandardCharsets.UTF_8),
                result,
                0);
        return result.getTask();
    }

    private static void attemptNearbyTargets(
            Context app,
            String path,
            byte[] payload,
            TaskCompletionSource<NearbySendResult> result,
            int failureIndex) {
        Wearable.getNodeClient(app)
                .getConnectedNodes()
                .addOnSuccessListener(nodes -> {
                    List<String> nodeIds = new ArrayList<>();
                    List<Task<Integer>> sends = new ArrayList<>();
                    for (Node node : nodes) {
                        if (!node.isNearby()) continue;
                        nodeIds.add(node.getId());
                        sends.add(Wearable.getMessageClient(app)
                                .sendMessage(node.getId(), path, payload));
                    }
                    if (sends.isEmpty()) {
                        scheduleNearbyRetry(
                                app, path, payload, result, failureIndex);
                        return;
                    }
                    Tasks.whenAllComplete(sends).addOnCompleteListener(ignored -> {
                        Set<String> successfulNodeIds = new LinkedHashSet<>();
                        for (int index = 0; index < sends.size(); index++) {
                            if (sends.get(index).isSuccessful()) {
                                successfulNodeIds.add(nodeIds.get(index));
                            }
                        }
                        if (!successfulNodeIds.isEmpty()) {
                            result.setResult(new NearbySendResult(successfulNodeIds));
                        } else {
                            scheduleNearbyRetry(
                                    app, path, payload, result, failureIndex);
                        }
                    });
                })
                .addOnFailureListener(error -> scheduleNearbyRetry(
                        app, path, payload, result, failureIndex));
    }

    private static void scheduleNearbyRetry(
            Context app,
            String path,
            byte[] payload,
            TaskCompletionSource<NearbySendResult> result,
            int failureIndex) {
        long delayMs = NearbyTargetRetryPolicy.delayAfterFailure(failureIndex);
        if (delayMs < 0L) {
            result.setResult(NearbySendResult.none());
            return;
        }
        new Handler(Looper.getMainLooper()).postDelayed(
                () -> attemptNearbyTargets(
                        app, path, payload, result, failureIndex + 1),
                delayMs);
    }

    /** Replays a prepared command to one already-selected nearby phone node. */
    public static Task<Boolean> sendToTarget(
            Context context,
            String nodeId,
            String path,
            String jsonPayload) {
        TaskCompletionSource<Boolean> result = new TaskCompletionSource<>();
        String target = nodeId == null ? "" : nodeId.trim();
        if (target.isEmpty()) {
            result.setResult(false);
            return result.getTask();
        }
        Context app = context.getApplicationContext();
        try {
            Wearable.getMessageClient(app)
                    .sendMessage(
                            target,
                            path,
                            jsonPayload.getBytes(StandardCharsets.UTF_8))
                    .addOnCompleteListener(task -> result.setResult(task.isSuccessful()));
        } catch (RuntimeException error) {
            result.setResult(false);
        }
        return result.getTask();
    }

    public static final class NearbySendResult {
        private final Set<String> successfulNodeIds;

        private NearbySendResult(Set<String> successfulNodeIds) {
            this.successfulNodeIds = Collections.unmodifiableSet(
                    new LinkedHashSet<>(successfulNodeIds));
        }

        private static NearbySendResult none() {
            return new NearbySendResult(Collections.emptySet());
        }

        public boolean anyQueued() {
            return !successfulNodeIds.isEmpty();
        }

        public Set<String> successfulNodeIds() {
            return successfulNodeIds;
        }
    }
}
