package com.shushunya.m;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.text.TextUtils;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import org.json.JSONObject;

/**
 * Real push receiver. Vox sends an FCM message when there is something urgent to
 * say; Google delivers it even when the app is fully closed — no foreground
 * service, no persistent notification. This just shows the notification and
 * keeps the device's FCM token registered with Vox.
 */
public class VoxMessagingService extends FirebaseMessagingService {
    private static final String ALERT_CHANNEL = "shushunya_answers";
    private static final int ALERT_ID = 1002;

    @Override
    public void onNewToken(String token) {
        registerToken(getApplicationContext(), token);
    }

    @Override
    public void onMessageReceived(RemoteMessage message) {
        String title = "Шушуня хочет что-то сказать";
        String body = "";
        if (message.getNotification() != null) {
            if (message.getNotification().getTitle() != null) {
                title = message.getNotification().getTitle();
            }
            if (message.getNotification().getBody() != null) {
                body = message.getNotification().getBody();
            }
        }
        if (body.isEmpty() && message.getData().containsKey("body")) {
            body = message.getData().get("body");
        }
        showAlert(title, body);
    }

    private void showAlert(String title, String body) {
        ensureChannel();
        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 9, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        String text = TextUtils.ellipsize(body == null ? "" : body, new android.text.TextPaint(), 600, TextUtils.TruncateAt.END).toString();
        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, ALERT_CHANNEL)
                : new Notification.Builder(this);
        b.setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(pi)
                .setAutoCancel(true);
        NotificationManager m = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (m != null) {
            m.notify(ALERT_ID, b.build());
        }
    }

    private void ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager m = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (m != null && m.getNotificationChannel(ALERT_CHANNEL) == null) {
            m.createNotificationChannel(new NotificationChannel(ALERT_CHANNEL, "Сообщения Шушуни", NotificationManager.IMPORTANCE_HIGH));
        }
    }

    static void registerToken(Context ctx, String token) {
        if (token == null || token.trim().isEmpty()) {
            return;
        }
        SharedPreferences prefs = ctx.getSharedPreferences("shushunya_m", Context.MODE_PRIVATE);
        String base = prefs.getString("base_url", "https://chat.shushunya.com");
        if (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }
        final String url = base + "/archive/client/chat/reports/register-token";
        final String payload = "{\"token\":\"" + token.trim() + "\"}";
        new Thread(() -> {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(12000);
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setRequestProperty("User-Agent", "ShushunyaPushRegister");
                String key = BuildConfig.CLIENT_API_KEY == null ? "" : BuildConfig.CLIENT_API_KEY.trim();
                if (!key.isEmpty()) {
                    conn.setRequestProperty("Authorization", "Bearer " + key);
                    conn.setRequestProperty("X-Shushunya-Client-Key", key);
                }
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(payload.getBytes(StandardCharsets.UTF_8));
                }
                conn.getResponseCode();
                conn.disconnect();
            } catch (Exception ignored) {
            }
        }).start();
    }
}
