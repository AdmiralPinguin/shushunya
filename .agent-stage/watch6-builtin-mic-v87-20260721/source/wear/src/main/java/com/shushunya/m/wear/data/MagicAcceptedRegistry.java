package com.shushunya.m.wear.data;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Process-local inbox for exact post-dispatch phone bridge ACKs. */
public final class MagicAcceptedRegistry {
    private static final int MAX_REQUESTS = 32;
    private static final Map<String, List<Ack>> ACKS = new HashMap<>();

    private MagicAcceptedRegistry() {}

    public static synchronized void record(
            String requestId,
            String sourceNodeId,
            long acknowledgedAtElapsedMs) {
        record(requestId, sourceNodeId, false, acknowledgedAtElapsedMs);
    }

    public static synchronized void record(
            String requestId,
            String sourceNodeId,
            boolean targetStart,
            long acknowledgedAtElapsedMs) {
        String request = MagicAcceptedPolicy.cleanRequestId(requestId);
        String source = MagicAcceptedPolicy.cleanNodeId(sourceNodeId);
        if (request.isEmpty() || source.isEmpty() || acknowledgedAtElapsedMs <= 0L) return;
        purge(acknowledgedAtElapsedMs);
        if (ACKS.size() >= MAX_REQUESTS && !ACKS.containsKey(request)) {
            Iterator<String> oldest = ACKS.keySet().iterator();
            if (oldest.hasNext()) ACKS.remove(oldest.next());
        }
        ACKS.computeIfAbsent(request, ignored -> new ArrayList<>())
                .add(new Ack(request, source, targetStart, acknowledgedAtElapsedMs));
    }

    public static synchronized String consumeMatching(
            String requestId,
            Set<String> allowedNodeIds,
            long nowElapsedMs) {
        String request = MagicAcceptedPolicy.cleanRequestId(requestId);
        purge(nowElapsedMs);
        AcceptedAck ack = consumeMatchingAck(request, allowedNodeIds, nowElapsedMs);
        return ack == null ? "" : ack.sourceNodeId;
    }

    public static synchronized AcceptedAck consumeMatchingAck(
            String requestId,
            Set<String> allowedNodeIds,
            long nowElapsedMs) {
        String request = MagicAcceptedPolicy.cleanRequestId(requestId);
        purge(nowElapsedMs);
        List<Ack> candidates = ACKS.get(request);
        if (candidates == null) return null;
        for (Ack candidate : candidates) {
            if (!MagicAcceptedPolicy.matches(
                    request,
                    allowedNodeIds,
                    candidate.requestId,
                    candidate.sourceNodeId,
                    candidate.acknowledgedAtElapsedMs,
                    nowElapsedMs)) continue;
            ACKS.remove(request);
            return new AcceptedAck(candidate.sourceNodeId, candidate.targetStart);
        }
        return null;
    }

    public static synchronized void discard(String requestId) {
        ACKS.remove(MagicAcceptedPolicy.cleanRequestId(requestId));
    }

    static synchronized void clearForTest() {
        ACKS.clear();
    }

    private static void purge(long nowElapsedMs) {
        Iterator<Map.Entry<String, List<Ack>>> entries = ACKS.entrySet().iterator();
        while (entries.hasNext()) {
            List<Ack> candidates = entries.next().getValue();
            candidates.removeIf(candidate -> MagicAcceptedPolicy.isStale(
                    candidate.acknowledgedAtElapsedMs, nowElapsedMs));
            if (candidates.isEmpty()) entries.remove();
        }
    }

    private static final class Ack {
        final String requestId;
        final String sourceNodeId;
        final boolean targetStart;
        final long acknowledgedAtElapsedMs;

        Ack(
                String requestId,
                String sourceNodeId,
                boolean targetStart,
                long acknowledgedAtElapsedMs) {
            this.requestId = requestId;
            this.sourceNodeId = sourceNodeId;
            this.targetStart = targetStart;
            this.acknowledgedAtElapsedMs = acknowledgedAtElapsedMs;
        }
    }

    public static final class AcceptedAck {
        public final String sourceNodeId;
        public final boolean targetStart;

        AcceptedAck(String sourceNodeId, boolean targetStart) {
            this.sourceNodeId = sourceNodeId;
            this.targetStart = targetStart;
        }
    }
}
