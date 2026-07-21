package com.shushunya.m.wear.data;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Tiny process-local inbox that closes the ACK-before-send-task callback race.
 * The Wear listener may receive PREPARED before MessageClient's local Task is
 * completed, so the Activity consumes it only after it knows the exact set of
 * nodes to which PREPARE was successfully queued.
 */
public final class PreparedAckRegistry {
    private static final int MAX_REQUESTS = 32;
    private static final Map<String, List<Ack>> ACKS = new HashMap<>();

    private PreparedAckRegistry() {}

    public static synchronized void record(
            String requestId,
            String sourceNodeId,
            long acknowledgedAtElapsedMs) {
        String request = PreparedAckPolicy.cleanRequestId(requestId);
        String source = PreparedAckPolicy.cleanNodeId(sourceNodeId);
        if (request.isEmpty() || source.isEmpty() || acknowledgedAtElapsedMs <= 0L) return;
        purge(acknowledgedAtElapsedMs);
        if (ACKS.size() >= MAX_REQUESTS && !ACKS.containsKey(request)) {
            Iterator<String> oldest = ACKS.keySet().iterator();
            if (oldest.hasNext()) ACKS.remove(oldest.next());
        }
        ACKS.computeIfAbsent(request, ignored -> new ArrayList<>())
                .add(new Ack(request, source, acknowledgedAtElapsedMs));
    }

    public static synchronized String consumeMatching(
            String requestId,
            Set<String> allowedNodeIds,
            long nowElapsedMs) {
        String request = PreparedAckPolicy.cleanRequestId(requestId);
        purge(nowElapsedMs);
        List<Ack> candidates = ACKS.get(request);
        if (candidates == null) return "";
        for (Ack candidate : candidates) {
            if (PreparedAckPolicy.matches(
                    request,
                    allowedNodeIds,
                    candidate.requestId,
                    candidate.sourceNodeId,
                    candidate.acknowledgedAtElapsedMs,
                    nowElapsedMs)) {
                ACKS.remove(request);
                return candidate.sourceNodeId;
            }
        }
        return "";
    }

    public static synchronized void discard(String requestId) {
        ACKS.remove(PreparedAckPolicy.cleanRequestId(requestId));
    }

    static synchronized void clearForTest() {
        ACKS.clear();
    }

    private static void purge(long nowElapsedMs) {
        Iterator<Map.Entry<String, List<Ack>>> entries = ACKS.entrySet().iterator();
        while (entries.hasNext()) {
            List<Ack> candidates = entries.next().getValue();
            candidates.removeIf(candidate -> PreparedAckPolicy.isStale(
                    candidate.acknowledgedAtElapsedMs, nowElapsedMs));
            if (candidates.isEmpty()) entries.remove();
        }
    }

    private static final class Ack {
        final String requestId;
        final String sourceNodeId;
        final long acknowledgedAtElapsedMs;

        Ack(String requestId, String sourceNodeId, long acknowledgedAtElapsedMs) {
            this.requestId = requestId;
            this.sourceNodeId = sourceNodeId;
            this.acknowledgedAtElapsedMs = acknowledgedAtElapsedMs;
        }
    }
}
