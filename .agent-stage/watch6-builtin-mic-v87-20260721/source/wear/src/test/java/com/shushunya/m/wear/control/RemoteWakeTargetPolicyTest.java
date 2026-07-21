package com.shushunya.m.wear.control;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public final class RemoteWakeTargetPolicyTest {
    @Test
    public void preparedAckIsPreferredWhenItAlreadyRacedTheSendTask() {
        RemoteWakeTargetPolicy.Selection selection = RemoteWakeTargetPolicy.select(
                "phone-b",
                new LinkedHashSet<>(List.of("phone-a", "phone-b")));

        assertTrue(selection.hasTarget());
        assertEquals("phone-b", selection.nodeId);
        assertTrue(selection.preparedAckObserved);
    }

    @Test
    public void queuedPhoneIsWokenWithoutWaitingForPreparedAck() {
        RemoteWakeTargetPolicy.Selection selection = RemoteWakeTargetPolicy.select(
                "",
                new LinkedHashSet<>(List.of("phone-a")));

        assertTrue(selection.hasTarget());
        assertEquals("phone-a", selection.nodeId);
        assertFalse(selection.preparedAckObserved);
    }

    @Test
    public void multipleQueuedTargetsWithoutMatchingAckFailClosed() {
        RemoteWakeTargetPolicy.Selection selection = RemoteWakeTargetPolicy.select(
                "attacker-node",
                new LinkedHashSet<>(List.of("phone-a", "phone-b")));

        assertFalse(selection.hasTarget());
    }

    @Test
    public void wrongAckCannotRedirectTheOnlyTransportTarget() {
        RemoteWakeTargetPolicy.Selection selection = RemoteWakeTargetPolicy.select(
                "attacker-node",
                new LinkedHashSet<>(List.of("phone-a")));

        assertEquals("phone-a", selection.nodeId);
        assertFalse(selection.preparedAckObserved);
    }

    @Test
    public void missingQueuedTargetCannotLaunchRemoteActivity() {
        assertFalse(RemoteWakeTargetPolicy.select("phone-a", Set.of()).hasTarget());
        assertFalse(RemoteWakeTargetPolicy.select("phone-a", null).hasTarget());
    }
}
