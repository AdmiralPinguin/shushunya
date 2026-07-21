package com.shushunya.m.wear.data;

import java.util.UUID;

public final class WearProtocol {
    public static final String PATH_LIVE_TOGGLE = "/shushunya/live/toggle";
    public static final String PATH_MUSIC_TOGGLE = "/shushunya/music/toggle";
    public static final String PATH_MAGIC_TOGGLE = "/shushunya/magic/toggle";
    public static final String PATH_MAGIC_PREPARE = "/shushunya/magic/prepare";
    public static final String PATH_MAGIC_PREPARED = "/shushunya/magic/prepared";
    public static final String PATH_MAGIC_ACCEPTED = "/shushunya/magic/accepted";
    public static final String PATH_STATE_REQUEST = "/shushunya/state/request";
    public static final String PATH_STATE = "/shushunya/state";

    private WearProtocol() {}

    public static Request newRequest() {
        String requestId = UUID.randomUUID().toString();
        long issuedAtMs = System.currentTimeMillis();
        // UUID contains only JSON-safe ASCII characters, so keeping this tiny
        // payload platform-independent also makes protocol tests meaningful on
        // the host JVM (where android.jar's JSONObject is only a stub).
        String payload = requestJson(requestId, issuedAtMs);
        return new Request(requestId, issuedAtMs, payload);
    }

    /** Reconstructs the byte-identical PREPARE payload after process death. */
    public static String requestJson(String requestId, long issuedAtMs) {
        String clean = requestId == null ? "" : requestId.trim();
        // All command ids are UUIDs generated above; reject anything that
        // could alter the tiny host-testable JSON envelope.
        if (!clean.matches("[A-Za-z0-9._:-]{1,256}")) return "";
        return "{\"requestId\":\"" + clean
                + "\",\"issuedAtMs\":" + issuedAtMs + "}";
    }

    /** Observational refreshes intentionally return an empty-id background snapshot. */
    public static String newStateQueryJson() {
        return "{\"requestId\":\"\",\"issuedAtMs\":"
                + System.currentTimeMillis() + "}";
    }

    public static final class Request {
        public final String id;
        public final long issuedAtMs;
        public final String json;

        private Request(String id, long issuedAtMs, String json) {
            this.id = id;
            this.issuedAtMs = issuedAtMs;
            this.json = json;
        }
    }
}
