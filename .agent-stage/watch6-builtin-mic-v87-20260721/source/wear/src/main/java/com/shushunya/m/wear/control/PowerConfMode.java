package com.shushunya.m.wear.control;

import android.content.Context;
import com.shushunya.m.wear.audio.WatchMicStatusNotification;
import com.shushunya.m.wear.data.ComplicationRefresh;
import com.shushunya.m.wear.data.ControllerStateStore;

/** Removes stale pre-v80 controller-only diagnostics without touching live capture. */
public final class PowerConfMode {
    private PowerConfMode() {}

    public static void enforce(Context context) {
        Context app = context.getApplicationContext();
        WatchMicStatusNotification.cancel(app);
        // Only clear an impossible stale bit when no v80 capture service is alive.
        if (!com.shushunya.m.wear.audio.WearMicForegroundService.isCaptureServiceActive()
                && ControllerStateStore.updateWatchMicrophone(app, false, "")) {
            ComplicationRefresh.request(app, ControllerStateStore.Kind.LIVE);
        }
    }
}
