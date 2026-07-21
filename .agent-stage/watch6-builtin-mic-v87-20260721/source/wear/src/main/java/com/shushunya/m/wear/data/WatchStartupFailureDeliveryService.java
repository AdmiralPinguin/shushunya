package com.shushunya.m.wear.data;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import com.shushunya.m.R;

/** OS-backed process anchor retained only while an exact failure awaits phone ACK. */
public final class WatchStartupFailureDeliveryService extends Service {
    private static final String ACTION_DELIVER =
            "com.shushunya.m.wear.data.DELIVER_STARTUP_FAILURE";
    private static final String CHANNEL_ID = "shushunya_failure_delivery";
    private static final int NOTIFICATION_ID = 1864;
    private static final long HARD_DEADLINE_MS = 30_000L;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private boolean deadlineScheduled;
    private final Runnable hardDeadline = this::stopBoundedAnchor;

    public static void start(Context context) {
        if (context == null) return;
        Context app = context.getApplicationContext();
        try {
            app.startForegroundService(new Intent(
                    app, WatchStartupFailureDeliveryService.class)
                    .setAction(ACTION_DELIVER));
        } catch (RuntimeException ignored) {
            // The already-running anchor or the next direct-boot/package event
            // will resume the durable outbox without losing its exact payload.
        }
    }

    public static void stop(Context context) {
        if (context == null) return;
        Context app = context.getApplicationContext();
        app.stopService(new Intent(app, WatchStartupFailureDeliveryService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "Доставка ошибки микрофона Watch6",
                    NotificationManager.IMPORTANCE_LOW);
            channel.setSound(null, null);
            channel.enableVibration(false);
            manager.createNotificationChannel(channel);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification notification = new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_shushunya)
                .setContentTitle("Шушуня · ошибка микрофона Watch6")
                .setContentText("Доставляю точную причину на телефон…")
                .setOnlyAlertOnce(true)
                .setOngoing(true)
                .build();
        startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        if (!WatchStartupFailureOutbox.hasPending(this)) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!deadlineScheduled) {
            deadlineScheduled = true;
            mainHandler.postDelayed(hardDeadline, HARD_DEADLINE_MS);
        }
        WatchStartupFailureOutbox.resume(this);
        return START_NOT_STICKY;
    }

    @Override
    public void onTimeout(int startId, int fgsType) {
        stopBoundedAnchor();
    }

    @Override
    public void onDestroy() {
        mainHandler.removeCallbacks(hardDeadline);
        super.onDestroy();
    }

    private void stopBoundedAnchor() {
        mainHandler.removeCallbacks(hardDeadline);
        WatchStartupFailureOutbox.cancelDeliveryWindow();
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
