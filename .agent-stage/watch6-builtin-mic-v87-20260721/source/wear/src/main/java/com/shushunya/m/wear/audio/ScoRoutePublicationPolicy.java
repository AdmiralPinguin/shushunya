package com.shushunya.m.wear.audio;

import java.util.Collections;
import java.util.List;

/** Pure policy for the eventually-consistent AudioManager SCO device list. */
final class ScoRoutePublicationPolicy {
    enum State { READY, MISSING, AMBIGUOUS, ADDRESS_MISMATCH }

    private ScoRoutePublicationPolicy() {}

    static State evaluate(List<String> scoAddresses, String expectedAddress) {
        List<String> addresses = scoAddresses == null
                ? Collections.emptyList() : scoAddresses;
        if (addresses.isEmpty()) return State.MISSING;
        if (addresses.size() != 1) return State.AMBIGUOUS;
        String actual = clean(addresses.get(0));
        String expected = clean(expectedAddress);
        // Some Wear audio HALs redact the address. Exact exclusive HFP ownership
        // plus a sole SCO endpoint remains sufficient in that case.
        if (actual.isEmpty() || expected.equalsIgnoreCase(actual)) return State.READY;
        return State.ADDRESS_MISMATCH;
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
