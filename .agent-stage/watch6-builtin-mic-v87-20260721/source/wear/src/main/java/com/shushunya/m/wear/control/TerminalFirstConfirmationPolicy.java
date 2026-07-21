package com.shushunya.m.wear.control;

/** Haptic fallback when cross-path Data Layer delivery reorders terminal before ACCEPTED. */
final class TerminalFirstConfirmationPolicy {
    enum Haptic { NONE, LIGHT_SUCCESS, FAILURE }

    private TerminalFirstConfirmationPolicy() {}

    static Haptic decide(
            boolean acceptedAlreadyDurable,
            boolean hasError,
            boolean exactStarted) {
        if (acceptedAlreadyDurable) return Haptic.NONE;
        if (hasError) return Haptic.FAILURE;
        // exactStarted immediately emits its dedicated strong haptic.
        return exactStarted ? Haptic.NONE : Haptic.LIGHT_SUCCESS;
    }
}
