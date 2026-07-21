package com.shushunya.m.wear.complication;

import android.os.RemoteException;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.wear.watchface.complications.data.ComplicationData;
import androidx.wear.watchface.complications.data.ComplicationType;
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceService;
import androidx.wear.watchface.complications.datasource.ComplicationRequest;

import com.shushunya.m.wear.control.StateSync;
import com.shushunya.m.wear.data.ControllerStateStore;

public final class MusicControlComplicationService extends ComplicationDataSourceService {
    @Override
    public void onComplicationRequest(
            @NonNull ComplicationRequest request,
            @NonNull ComplicationRequestListener listener) {
        try {
            listener.onComplicationData(ComplicationUiFactory.build(
                    this,
                    ControllerStateStore.Kind.MUSIC,
                    request.getComplicationType(),
                    false));
        } catch (RemoteException ignored) {
            // The watch face process disappeared before it accepted the update.
        }
    }

    @Nullable
    @Override
    public ComplicationData getPreviewData(@NonNull ComplicationType type) {
        return ComplicationUiFactory.build(
                this, ControllerStateStore.Kind.MUSIC, type, true);
    }

    @Override
    public void onComplicationActivated(
            int complicationInstanceId,
            @NonNull ComplicationType type) {
        super.onComplicationActivated(complicationInstanceId, type);
        StateSync.request(this);
    }
}
