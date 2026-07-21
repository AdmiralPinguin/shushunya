package com.shushunya.m.wear.control;

/** Pure routing policy for a user-initiated Watch command. */
final class MagicDispatchPolicy {
    enum Route {
        DATA_LAYER_TOGGLE,
        REMOTE_PHONE_MAGIC_TOGGLE
    }

    private MagicDispatchPolicy() {}

    static Route route(boolean magicAction) {
        // The Watch cache can be stale after the phone process is killed or an
        // APK is updated. The visible phone bridge therefore owns every MAGIC
        // toggle and decides from the phone service's real in-memory state.
        return magicAction
                ? Route.REMOTE_PHONE_MAGIC_TOGGLE
                : Route.DATA_LAYER_TOGGLE;
    }
}
