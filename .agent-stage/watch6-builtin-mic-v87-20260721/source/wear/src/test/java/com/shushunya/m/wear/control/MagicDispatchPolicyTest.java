package com.shushunya.m.wear.control;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class MagicDispatchPolicyTest {
    @Test
    public void everyMagicTapUsesPhoneOwnedToggle() {
        assertEquals(
                MagicDispatchPolicy.Route.REMOTE_PHONE_MAGIC_TOGGLE,
                MagicDispatchPolicy.route(true));
    }

    @Test
    public void explicitLiveButtonKeepsItsPlainToggleSemantics() {
        assertEquals(
                MagicDispatchPolicy.Route.DATA_LAYER_TOGGLE,
                MagicDispatchPolicy.route(false));
    }
}
