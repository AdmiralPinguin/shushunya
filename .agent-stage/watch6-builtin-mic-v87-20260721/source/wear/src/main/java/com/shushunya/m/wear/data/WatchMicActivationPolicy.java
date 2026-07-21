package com.shushunya.m.wear.data;

/**
 * Keeps a phone-live acknowledgement from racing the local microphone FGS.
 * Every phone observation, including an error, passes this exact-correlation policy.
 */
final class WatchMicActivationPolicy {
    enum Action {
        CANCEL_WARNING,
        SCHEDULE_RECHECK,
        REPORT_MISSING
    }

    enum CaptureAction {
        KEEP_CAPTURE,
        STOP_CAPTURE
    }

    private WatchMicActivationPolicy() {}

    static Action decide(boolean phoneLive, boolean captureServiceActive, boolean graceExpired) {
        if (!phoneLive || captureServiceActive) return Action.CANCEL_WARNING;
        return graceExpired ? Action.REPORT_MISSING : Action.SCHEDULE_RECHECK;
    }

    /**
     * Phone lifecycle snapshots are observations, not microphone commands. In
     * particular, an asynchronous "stopped" snapshot can race the matching
     * acknowledgement for a Watch start command. Stopping capture for that
     * transient snapshot tears down AudioRecord before the phone can accept its
     * channel.
     *
     * Capture is therefore stopped only by the result of the exact pending Watch
     * command (successful toggle-off or failed toggle-on). A failed toggle-off
     * keeps the existing capture because the phone never committed its STOP.
     * A background paused/error/stopped snapshot leaves the already user-started
     * microphone FGS alive so its own
     * channel loop can reconnect without another user gesture.
     */
    static CaptureAction captureAction(
            boolean phoneLive,
            boolean matchingWatchCommand,
            boolean exactTerminal,
            boolean targetKnown,
            boolean targetStart,
            boolean terminalFailed) {
        if (!matchingWatchCommand || !exactTerminal || !targetKnown) {
            return CaptureAction.KEEP_CAPTURE;
        }
        if (targetStart) {
            if (terminalFailed || !phoneLive) return CaptureAction.STOP_CAPTURE;
            return CaptureAction.KEEP_CAPTURE;
        }
        if (!terminalFailed) return CaptureAction.STOP_CAPTURE;
        return CaptureAction.KEEP_CAPTURE;
    }
}
