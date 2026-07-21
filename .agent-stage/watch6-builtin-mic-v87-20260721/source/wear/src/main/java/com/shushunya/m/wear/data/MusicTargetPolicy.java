package com.shushunya.m.wear.data;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** Fails closed unless discovery identifies one exact nearby phone node. */
final class MusicTargetPolicy {
    private MusicTargetPolicy() {}

    static String selectOneNearby(List<Candidate> candidates) {
        Set<String> nearby = new LinkedHashSet<>();
        if (candidates != null) {
            for (Candidate candidate : candidates) {
                if (candidate == null || !candidate.nearby) continue;
                String nodeId = clean(candidate.nodeId);
                if (!nodeId.isEmpty()) nearby.add(nodeId);
            }
        }
        return nearby.size() == 1 ? nearby.iterator().next() : "";
    }

    static final class Candidate {
        final String nodeId;
        final boolean nearby;

        Candidate(String nodeId, boolean nearby) {
            this.nodeId = clean(nodeId);
            this.nearby = nearby;
        }
    }

    private static String clean(String value) {
        if (value == null) return "";
        String clean = value.trim();
        return clean.length() <= 256 ? clean : "";
    }
}
