package com.shushunya.m.wear.control;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.IBinder;

import androidx.annotation.Nullable;

import com.shushunya.m.R;
import com.shushunya.m.wear.data.ControllerStateStore;
import com.shushunya.m.wear.data.MusicCommandCoordinator;

/** Short dataSync anchor for the standalone MUSIC delivery budget. */
public final class MusicCommandForegroundService extends Service {
    private static final String ACTION_KEEP_ALIVE =
            "com.shushunya.m.wear.control.KEEP_MUSIC_COMMAND_ALIVE";
    private static final String EXTRA_REQUEST_ID = "request_id";
    private static final String CHANNEL_ID = "shushunya_music_command";
    private static final int NOTIFICATION_ID = 6125;

    public static void startPending(Context context, String requestId) {
        String exactRequest = clean(requestId);
        if (exactRequest.isEmpty()) throw new IllegalArgumentException("requestId is empty");
        context.startForegroundService(new Intent(context, MusicCommandForegroundService.class)
                .setAction(ACTION_KEEP_ALIVE)
                .putExtra(EXTRA_REQUEST_ID, exactRequest));
    }

    public static void stop(Context context) {
        context.stopService(new Intent(context, MusicCommandForegroundService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID,
                    "Shushunya music command",
                    NotificationManager.IMPORTANCE_LOW));
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(
                NOTIFICATION_ID,
                new Notification.Builder(this, CHANNEL_ID)
                        .setSmallIcon(R.drawable.ic_shushunya)
                        .setContentTitle("Shushunya")
                        .setContentText("Sending music control to the phone")
                        .setOngoing(true)
                        .setOnlyAlertOnce(true)
                        .setCategory(Notification.CATEGORY_SERVICE)
                        .build(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);

        String requestId = intent == null
                ? ""
                : clean(intent.getStringExtra(EXTRA_REQUEST_ID));
        String durableRequest = MusicCommandCoordinator.pendingRequestId(this);
        if (requestId.isEmpty()) requestId = durableRequest;
        if (requestId.isEmpty()
                || !requestId.equals(durableRequest)
                || !ControllerStateStore.isMatchingPending(
                        this, ControllerStateStore.Kind.MUSIC, requestId)) {
            stopSelfResult(startId);
            return START_NOT_STICKY;
        }
        MusicCommandCoordinator.resumeIfNeeded(this);
        return START_REDELIVER_INTENT;
    }

    @Override
    public void onTimeout(int startId, int fgsType) {
        stopSelfResult(startId);
    }

    @Override
    public void onDestroy() {
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
