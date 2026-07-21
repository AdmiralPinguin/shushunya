package com.shushunya.m.wear.control;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class TerminalFirstConfirmationPolicyTest {
    @Test
    public void reorderedStopAndErrorStillReceiveOneConfirmation() {
        assertEquals(
                TerminalFirstConfirmationPolicy.Haptic.LIGHT_SUCCESS,
                TerminalFirstConfirmationPolicy.decide(false, false, false));
        assertEquals(
                TerminalFirstConfirmationPolicy.Haptic.FAILURE,
                TerminalFirstConfirmationPolicy.decide(false, true, false));
        assertEquals(
                TerminalFirstConfirmationPolicy.Haptic.NONE,
                TerminalFirstConfirmationPolicy.decide(false, false, true));
        assertEquals(
                TerminalFirstConfirmationPolicy.Haptic.NONE,
                TerminalFirstConfirmationPolicy.decide(true, false, false));
        assertEquals(
                TerminalFirstConfirmationPolicy.Haptic.NONE,
                TerminalFirstConfirmationPolicy.decide(true, true, false));
    }
}
