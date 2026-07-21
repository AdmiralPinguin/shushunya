package com.shushunya.m.wear.data;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import com.shushunya.m.wear.audio.PrivateTtsPlaybackService;
import com.shushunya.m.wear.control.PowerConfMode;

/** Refreshes both wrapped complication actions after an in-place APK update. */
public final class PackageReplacedReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())) return;
        PowerConfMode.enforce(context);
        PrivateTtsPlaybackService.stop(context);
        WatchStartupFailureOutbox.ensureDelivery(context);
        ComplicationRefresh.requestAll(context);
    }
}
