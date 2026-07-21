package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class WearProtocolTest {
    @Test
    public void phoneAndWatchPreparePathMatches() {
        assertEquals("/shushunya/magic/prepare", WearProtocol.PATH_MAGIC_PREPARE);
        assertEquals("/shushunya/magic/prepared", WearProtocol.PATH_MAGIC_PREPARED);
        assertEquals("/shushunya/magic/accepted", WearProtocol.PATH_MAGIC_ACCEPTED);
        assertEquals("/shushunya/magic/toggle", WearProtocol.PATH_MAGIC_TOGGLE);
    }

    @Test
    public void preparedCommandCarriesItsCorrelationId() throws Exception {
        WearProtocol.Request request = WearProtocol.newRequest();
        assertFalse(request.id.isEmpty());
        assertFalse(request.issuedAtMs <= 0L);
        assertEquals(
                "{\"requestId\":\"" + request.id + "\",\"issuedAtMs\":"
                        + request.issuedAtMs + "}",
                request.json);
        assertEquals(
                request.json,
                WearProtocol.requestJson(request.id, request.issuedAtMs));
    }

    @Test
    public void observationalStateQueryUsesBackgroundSnapshotCorrelation() {
        String query = WearProtocol.newStateQueryJson();
        assertTrue(query.startsWith("{\"requestId\":\"\",\"issuedAtMs\":"));
        assertTrue(query.endsWith("}"));
    }
}
