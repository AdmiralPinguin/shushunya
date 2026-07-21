package com.shushunya.m.wear.data;

import android.content.ComponentName;
import android.content.Context;

import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester;

import com.shushunya.m.wear.complication.LiveTranslationComplicationService;
import com.shushunya.m.wear.complication.MusicControlComplicationService;

public final class ComplicationRefresh {
    private ComplicationRefresh() {}

    public static void request(Context context, ControllerStateStore.Kind kind) {
        Class<?> service = kind == ControllerStateStore.Kind.LIVE
                ? LiveTranslationComplicationService.class
                : MusicControlComplicationService.class;
        ComplicationDataSourceUpdateRequester.create(
                context.getApplicationContext(),
                new ComponentName(context, service))
                .requestUpdateAll();
    }

    public static void requestAll(Context context) {
        request(context, ControllerStateStore.Kind.LIVE);
        request(context, ControllerStateStore.Kind.MUSIC);
    }
}
