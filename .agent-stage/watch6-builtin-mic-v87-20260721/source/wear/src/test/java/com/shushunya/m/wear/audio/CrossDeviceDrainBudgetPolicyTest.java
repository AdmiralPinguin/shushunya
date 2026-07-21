package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class CrossDeviceDrainBudgetPolicyTest {
    @Test
    public void worstPhoneDeliveryStillLeavesMarginAfterDrainAndFullAckWindow() {
        assertEquals(500L, CrossDeviceDrainBudgetPolicy.worstCasePhoneMarginMs());
        assertTrue(
                CrossDeviceDrainBudgetPolicy.PHONE_LAST_DRAIN_RESEND_MS
                        + CrossDeviceDrainBudgetPolicy.PCM_DRAIN_MS
                        + CrossDeviceDrainBudgetPolicy.TERMINAL_ACK_MS
                        < CrossDeviceDrainBudgetPolicy.PHONE_GATE_MS);
    }

    @Test
    public void normalTailGetsAllFiveSecondsWithoutExtendingTotalDeadline() {
        long acceptedAt = 10_000L;
        assertEquals(5_000L, CrossDeviceDrainBudgetPolicy.ackBudgetMs(
                acceptedAt, acceptedAt + 240L));
        assertEquals(1_000L, CrossDeviceDrainBudgetPolicy.ackBudgetMs(
                acceptedAt, acceptedAt + 5_000L));
        assertEquals(0L, CrossDeviceDrainBudgetPolicy.ackBudgetMs(
                acceptedAt, acceptedAt + 6_001L));
        assertEquals(acceptedAt + 6_000L,
                CrossDeviceDrainBudgetPolicy.acceptedDeadlineMs(acceptedAt));
    }

    @Test
    public void replacementBindingWaitConsumesRatherThanExtendsStopBudget() {
        long acceptedAt = 20_000L;
        assertEquals(1_500L, CrossDeviceDrainBudgetPolicy.bindingWaitBudgetMs(
                1_500L, acceptedAt, acceptedAt + 1_000L));
        assertEquals(250L, CrossDeviceDrainBudgetPolicy.bindingWaitBudgetMs(
                1_500L, acceptedAt, acceptedAt + 5_750L));
    }
}
