package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class LiveTransitionPolicyTest {
    @Test
    public void pausedStoppedUnknownAndErrorStartFromHydraTowardChaos() {
        assertEquals(
                ControllerStateStore.LivePhase.STARTING,
                LiveTransitionPolicy.directionFor(ControllerStateStore.State.PAUSED));
        assertEquals(
                ControllerStateStore.LivePhase.STARTING,
                LiveTransitionPolicy.directionFor(ControllerStateStore.State.STOPPED));
        assertEquals(
                ControllerStateStore.LivePhase.STARTING,
                LiveTransitionPolicy.directionFor(ControllerStateStore.State.UNKNOWN));
        assertEquals(
                ControllerStateStore.LivePhase.STARTING,
                LiveTransitionPolicy.directionFor(ControllerStateStore.State.ERROR));
        assertFalse(LiveTransitionPolicy.sourceIconIsChaos(
                ControllerStateStore.LivePhase.STARTING,
                ControllerStateStore.State.STOPPED));
    }

    @Test
    public void onlyRunningStopsFromChaosTowardHydra() {
        assertEquals(
                ControllerStateStore.LivePhase.STOPPING,
                LiveTransitionPolicy.directionFor(ControllerStateStore.State.RUNNING));
        assertTrue(LiveTransitionPolicy.sourceIconIsChaos(
                ControllerStateStore.LivePhase.STOPPING,
                ControllerStateStore.State.RUNNING));
        assertFalse(LiveTransitionPolicy.sourceIconIsChaos(
                ControllerStateStore.LivePhase.NONE,
                ControllerStateStore.State.PAUSED));
    }

    @Test
    public void confirmationHoldsPhaseForAtLeastSevenHundredFiftyMilliseconds() {
        long startedAt = 10_000L;
        assertEquals(
                -1L,
                LiveTransitionPolicy.clearDelayMs(
                        ControllerStateStore.LivePhase.STARTING,
                        false,
                        startedAt,
                        startedAt + 2_000L));
        assertEquals(
                250L,
                LiveTransitionPolicy.clearDelayMs(
                        ControllerStateStore.LivePhase.STARTING,
                        true,
                        startedAt,
                        startedAt + 500L));
        assertFalse(LiveTransitionPolicy.canClear(
                ControllerStateStore.LivePhase.STARTING,
                true,
                startedAt,
                startedAt + 749L));
        assertTrue(LiveTransitionPolicy.canClear(
                ControllerStateStore.LivePhase.STARTING,
                true,
                startedAt,
                startedAt + 750L));
    }

    @Test
    public void clearingPhaseRevertsToLastConfirmedIconAfterErrorOrOffline() {
        assertFalse(LiveTransitionPolicy.sourceIconIsChaos(
                ControllerStateStore.LivePhase.NONE,
                ControllerStateStore.State.STOPPED));
        assertTrue(LiveTransitionPolicy.sourceIconIsChaos(
                ControllerStateStore.LivePhase.NONE,
                ControllerStateStore.State.RUNNING));
    }

    @Test
    public void pendingAndDebounceRejectTapsBeforeAnimationCanStart() {
        long now = 20_000L;
        assertFalse(LiveTransitionPolicy.acceptsTap(
                0L, 19_000L, now, 12_000L, 700L));
        assertFalse(LiveTransitionPolicy.acceptsTap(
                19_500L, 0L, now, 12_000L, 700L));
        assertTrue(LiveTransitionPolicy.acceptsTap(
                19_000L, 1_000L, now, 12_000L, 700L));
    }
}
