package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;

import org.junit.Test;

import java.nio.charset.StandardCharsets;

public final class MusicCommandStateTest {
    @Test
    public void everyRetryStateRetainsByteIdenticalUuidAndJson() {
        WearProtocol.Request request = WearProtocol.newRequest();
        MusicCommandState initial = MusicCommandState.create(request, 10_000L);
        assertNotNull(initial);
        MusicCommandState bound = initial.withPhoneNode("phone-1");
        MusicCommandState retried = bound.withNextAttempt(4);
        assertEquals(request.id, retried.requestId);
        assertEquals(
                new String(request.json.getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8),
                retried.jsonPayload);
        assertEquals("phone-1", retried.phoneNodeId);
        assertEquals(4, retried.nextAttemptIndex);
    }

    @Test
    public void corruptedDurableEnvelopeIsRejected() {
        WearProtocol.Request request = WearProtocol.newRequest();
        assertNull(MusicCommandState.restore(
                request.id,
                request.issuedAtMs,
                request.json + " ",
                10_000L,
                18_000L,
                "phone-1",
                0));
        assertNull(MusicCommandState.restore(
                request.id,
                request.issuedAtMs,
                request.json,
                10_000L,
                18_001L,
                "phone-1",
                0));
        assertNull(MusicCommandState.restore(
                request.id,
                request.issuedAtMs,
                request.json,
                10_000L,
                18_000L,
                "x".repeat(257),
                0));
    }
}
