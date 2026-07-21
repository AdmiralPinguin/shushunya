package com.shushunya.m.wear.data;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ResponseCorrelationTest {
    @Test
    public void asyncStateIsAlwaysApplied() {
        assertTrue(ResponseCorrelation.shouldApplyState("newer-command", "older", ""));
    }

    @Test
    public void matchingCommandStateIsApplied() {
        assertTrue(ResponseCorrelation.shouldApplyState(
                "request-1", "older", "request-1"));
    }

    @Test
    public void staleCommandStateDoesNotOverwriteNewerPendingCommand() {
        assertFalse(ResponseCorrelation.shouldApplyState(
                "request-2", "request-1", "request-1"));
    }

    @Test
    public void nonemptyStateWithoutPendingRequiresLatestAuthoritativeRequest() {
        assertTrue(ResponseCorrelation.shouldApplyState(
                "", "request-b", "request-b"));
        assertFalse(ResponseCorrelation.shouldApplyState(
                "", "request-b", "request-a"));
        assertFalse(ResponseCorrelation.shouldApplyState(
                "", "", "old-request"));
    }

    @Test
    public void reorderedAFinalCannotOverwriteOrStopCurrentB() {
        // B is current/pending: late A is rejected before updateLive and its
        // microphone stop policy can run.
        assertFalse(ResponseCorrelation.shouldApplyState(
                "request-b", "request-a", "request-a"));
        assertTrue(ResponseCorrelation.shouldApplyState(
                "request-b", "request-a", "request-b"));
        // After B final, only B duplicates remain authoritative.
        assertFalse(ResponseCorrelation.shouldApplyState(
                "", "request-b", "request-a"));
    }

    @Test
    public void commandErrorRequiresExactNonEmptyRequestId() {
        assertTrue(ResponseCorrelation.isMatchingCommandError("request-1", "request-1"));
        assertFalse(ResponseCorrelation.isMatchingCommandError("request-2", "request-1"));
        assertFalse(ResponseCorrelation.isMatchingCommandError("request-1", ""));
        assertFalse(ResponseCorrelation.isMatchingCommandError("", "request-1"));
    }

    @Test
    public void transitionConfirmationRequiresTheExactPendingRequest() {
        assertTrue(ResponseCorrelation.isMatchingCommandError(
                "phase-request", "phase-request"));
        assertFalse(ResponseCorrelation.isMatchingCommandError(
                "new-phase-request", "stale-request"));
        assertFalse(ResponseCorrelation.isMatchingCommandError(
                "phase-request", ""));
    }

    @Test
    public void asyncStateOnlyClearsAnExpiredPendingCommand() {
        long now = 20_000L;
        long timeout = 10_000L;
        assertFalse(ResponseCorrelation.shouldClearPending(
                "request-1", 15_000L, "", now, timeout));
        assertTrue(ResponseCorrelation.shouldClearPending(
                "request-1", 5_000L, "", now, timeout));
    }

    @Test
    public void matchingResponseClearsPendingAndStaleResponseDoesNot() {
        assertTrue(ResponseCorrelation.shouldClearPending(
                "request-1", 1L, "request-1", 2L, 10_000L));
        assertFalse(ResponseCorrelation.shouldClearPending(
                "request-2", 1L, "request-1", 2L, 10_000L));
    }
}
