package com.shushunya.m.wear.audio;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.util.Log;

import com.shushunya.m.R;

/** One-shot explanation for a phone-started translator with no Watch PCM stream. */
public final class WatchMicStatusNotification {
    private static final String TAG = "ShushunyaWatchMic";
    private static final String CHANNEL_ID = "shushunya_watch_microphone_attention";
    private static final int NOTIFICATION_ID = 1862;

    private WatchMicStatusNotification() {}

    public static void showPhoneOnly(Context context) {
        Context app = context.getApplicationContext();
        String explanation = "Перевод запущен с телефона; микрофон часов не включён. "
                + "Для двух дорожек остановите и снова запустите перевод кнопкой на часах.";
        Log.w(TAG, "PHONE_ONLY: Watch microphone FGS is inactive; visible Watch action required");
        if (app.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "PHONE_ONLY notification permission is absent; state remains in complication data");
            return;
        }
        NotificationManager manager = app.getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Состояние микрофона часов",
                NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription("Предупреждение, когда перевод запущен без дорожки Watch6");
        manager.createNotificationChannel(channel);
        manager.notify(
                NOTIFICATION_ID,
                new Notification.Builder(app, CHANNEL_ID)
                        .setSmallIcon(R.drawable.ic_shushunya)
                        .setContentTitle("Шушуня · нет дорожки часов")
                        .setContentText(explanation)
                        .setStyle(new Notification.BigTextStyle().bigText(explanation))
                        .setOnlyAlertOnce(true)
                        .setAutoCancel(true)
                        .build());
    }

    public static void cancel(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null) manager.cancel(NOTIFICATION_ID);
    }
}
