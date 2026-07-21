package com.shushunya.m.wear.control;

/** Pure gate for replaying a durable phone ACCEPTED direction after recovery. */
final class AcceptedActionReplayPolicy {
    private AcceptedActionReplayPolicy() {}

    static boolean shouldReplay(boolean accepted, boolean consumed, boolean exactPending) {
        return accepted && !consumed && exactPending;
    }
}
