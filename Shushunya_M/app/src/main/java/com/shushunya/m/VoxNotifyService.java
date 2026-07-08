package com.shushunya.m;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;
import android.text.TextUtils;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Foreground service that keeps buzzing the phone about urgent Vox intents even
 * when the app is closed or backgrounded — notifications must not depend on the
 * Activity being alive. It long-polls Vox /announce (which marks announced
 * server-side, so the phone stays stateless) and posts an OS notification when
 * Vox says to. A quiet persistent notification keeps the service alive.
 */
public class VoxNotifyService extends Service {
    private static final String ONGOING_CHANNEL = "shushunya_vox_service";
    private static final String ALERT_CHANNEL = "shushunya_answers";
    private static final int ONGOING_ID = 1010;
    private static final int ALERT_ID = 1002;
    private volatile boolean running;
    private Thread worker;

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannels();
        startForeground(ONGOING_ID, buildOngoing());
        running = true;
        worker = new Thread(this::loop, "vox-notify");
        worker.start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;  // Android restarts it if killed
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) {
            worker.interrupt();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private String baseUrl() {
        SharedPreferences prefs = getSharedPreferences("shushunya_m", MODE_PRIVATE);
        String url = prefs.getString("base_url", "https://chat.shushunya.com");
        return url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }

    private void applyAuth(HttpURLConnection conn) {
        conn.setRequestProperty("User-Agent", "ShushunyaVoxService");
        String key = BuildConfig.CLIENT_API_KEY == null ? "" : BuildConfig.CLIENT_API_KEY.trim();
        if (!key.isEmpty()) {
            conn.setRequestProperty("Authorization", "Bearer " + key);
            conn.setRequestProperty("X-Shushunya-Client-Key", key);
        }
    }

    private void loop() {
        while (running) {
            try {
                URL url = new URL(baseUrl() + "/archive/client/chat/reports/announce");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(35000);
                applyAuth(conn);
                if (conn.getResponseCode() >= 200 && conn.getResponseCode() < 300) {
                    JSONObject payload = new JSONObject(readAll(conn.getInputStream()));
                    if (payload.optBoolean("notify", false)) {
                        JSONArray lines = payload.optJSONArray("notify_lines");
                        StringBuilder body = new StringBuilder();
                        for (int i = 0; lines != null && i < lines.length(); i++) {
                            String line = lines.optString(i, "").trim();
                            if (!line.isEmpty()) {
                                if (body.length() > 0) {
                                    body.append('\n');
                                }
                                body.append(line);
                            }
                        }
                        if (body.length() > 0) {
                            postAlert(body.toString());
                        }
                    }
                }
                Thread.sleep(20000);
            } catch (InterruptedException stop) {
                break;
            } catch (Exception transient_) {
                try {
                    Thread.sleep(15000);
                } catch (InterruptedException stop) {
                    break;
                }
            }
        }
    }

    private void postAlert(String body) {
        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 9, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        String text = TextUtils.ellipsize(body, new android.text.TextPaint(), 600, TextUtils.TruncateAt.END).toString();
        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, ALERT_CHANNEL)
                : new Notification.Builder(this);
        b.setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentTitle("Шушуня хочет что-то сказать")
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(pi)
                .setAutoCancel(true);
        NotificationManager m = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (m != null) {
            m.notify(ALERT_ID, b.build());
        }
    }

    private Notification buildOngoing() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 10, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, ONGOING_CHANNEL)
                : new Notification.Builder(this);
        return b.setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentTitle("Шушуня на связи")
                .setContentText("Слежу за срочным")
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
    }

    private void ensureChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager m = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (m == null) {
            return;
        }
        NotificationChannel ongoing = new NotificationChannel(ONGOING_CHANNEL, "Связь Шушуни", NotificationManager.IMPORTANCE_MIN);
        ongoing.setShowBadge(false);
        m.createNotificationChannel(ongoing);
        NotificationChannel alert = new NotificationChannel(ALERT_CHANNEL, "Сообщения Шушуни", NotificationManager.IMPORTANCE_HIGH);
        m.createNotificationChannel(alert);
    }

    private String readAll(InputStream stream) throws Exception {
        StringBuilder sb = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                sb.append(line).append('\n');
            }
        }
        return sb.toString();
    }
}
