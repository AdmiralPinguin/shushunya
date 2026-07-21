package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class MagicSettlePolicyTest {
    @Test
    public void onlyExactFinalRunningAndEngagedStartsSettle() {
        assertTrue(MagicSettlePolicy.isExactStartedState(
                true, true, false, "running", true, true));
        assertFalse(MagicSettlePolicy.isExactStartedState(
                false, true, false, "running", true, true));
        assertFalse(MagicSettlePolicy.isExactStartedState(
                true, true, false, "starting", true, true));
        assertFalse(MagicSettlePolicy.isExactStartedState(
                true, true, false, "running", true, false));
        assertFalse(MagicSettlePolicy.isExactStartedState(
                true, true, true, "running", true, true));
    }

    @Test
    public void settleBlocksForTwoSecondsWithoutTapExtension() {
        long lockUntil = MagicSettlePolicy.lockUntil(10_000L);
        assertEquals(12_000L, lockUntil);
        assertTrue(MagicSettlePolicy.blocksTap(lockUntil, 10_001L));
        assertTrue(MagicSettlePolicy.blocksTap(lockUntil, 11_999L));
        assertFalse(MagicSettlePolicy.blocksTap(lockUntil, 12_000L));
        assertFalse(MagicSettlePolicy.blocksTap(lockUntil, 9_999L));
        assertFalse(MagicSettlePolicy.blocksTap(lockUntil, 1L));
        // Policy is read-only: ignored taps receive no candidate timestamp and
        // therefore cannot move the original lockUntil value.
        assertEquals(12_000L, lockUntil);
    }

    @Test
    public void finalHapticIsOneShotPerRequest() {
        assertTrue(MagicSettlePolicy.isNewConfirmation("old", "new"));
        assertFalse(MagicSettlePolicy.isNewConfirmation("new", "new"));
        assertFalse(MagicSettlePolicy.isNewConfirmation("old", "  "));
    }
}
