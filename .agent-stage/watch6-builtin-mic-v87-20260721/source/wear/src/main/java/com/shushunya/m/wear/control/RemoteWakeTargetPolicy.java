package com.shushunya.m.wear.control;

import java.util.LinkedHashSet;
import java.util.Set;

/** Selects the phone to wake without making PREPARED ACK a cold-start gate. */
final class RemoteWakeTargetPolicy {
    private RemoteWakeTargetPolicy() {}

    static Selection select(String preparedNodeId, Set<String> queuedNodeIds) {
        String prepared = clean(preparedNodeId);
        if (queuedNodeIds == null || queuedNodeIds.isEmpty()) return Selection.none();

        Set<String> cleanQueued = new LinkedHashSet<>();
        for (String rawNodeId : queuedNodeIds) {
            String nodeId = clean(rawNodeId);
            if (nodeId.isEmpty()) continue;
            cleanQueued.add(nodeId);
        }
        if (!prepared.isEmpty() && cleanQueued.contains(prepared)) {
            return new Selection(prepared, true);
        }
        // A no-ACK wake is safe only when transport identified one exact
        // nearby target. Never guess between multiple paired nodes.
        if (cleanQueued.size() != 1) return Selection.none();
        return new Selection(cleanQueued.iterator().next(), false);
    }

    private static String clean(String value) {
        String clean = value == null ? "" : value.trim();
        return clean.length() > 256 ? clean.substring(0, 256) : clean;
    }

    static final class Selection {
        final String nodeId;
        final boolean preparedAckObserved;

        private Selection(String nodeId, boolean preparedAckObserved) {
            this.nodeId = nodeId;
            this.preparedAckObserved = preparedAckObserved;
        }

        static Selection none() {
            return new Selection("", false);
        }

        boolean hasTarget() {
            return !nodeId.isEmpty();
        }
    }
}
