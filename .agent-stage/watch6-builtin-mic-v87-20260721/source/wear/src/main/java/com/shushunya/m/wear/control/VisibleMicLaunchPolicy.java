package com.shushunya.m.wear.control;

/** Pure Android 14 gate for applying a durable accepted microphone action. */
final class VisibleMicLaunchPolicy {
    enum Action { START, WAIT_FOR_PHONE_DRAIN, DEFER }

    private VisibleMicLaunchPolicy() {}

    static Action decide(
            boolean targetStart,
            boolean resumed,
            boolean finishing,
            boolean destroyed) {
        if (!targetStart) return Action.WAIT_FOR_PHONE_DRAIN;
        return resumed && !finishing && !destroyed ? Action.START : Action.DEFER;
    }
}
