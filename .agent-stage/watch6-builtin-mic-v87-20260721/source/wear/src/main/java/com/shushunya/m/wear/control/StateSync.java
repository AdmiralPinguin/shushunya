package com.shushunya.m.wear.control;

import android.content.Context;

import com.shushunya.m.wear.data.ControllerStateStore;
import com.shushunya.m.wear.data.WearMessageSender;
import com.shushunya.m.wear.data.WearProtocol;

public final class StateSync {
    private StateSync() {}

    public static void request(Context context) {
        if (!ControllerStateStore.shouldRequestState(context)) return;
        WearMessageSender.sendToNearby(
                context,
                WearProtocol.PATH_STATE_REQUEST,
                WearProtocol.newStateQueryJson());
    }
}
