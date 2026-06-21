package com.mechanicum.roxdub;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class StatusForegroundService extends Service {
    public static final String PREFS = "roxdub";
    public static final String KEY_URL = "server_url";
    public static final String KEY_TOKEN = "token";
    private static final String CHANNEL_ID = "roxdub_status";
    private static final int NOTIFICATION_ID = 41;

    private ScheduledExecutorService executor;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        startForeground(NOTIFICATION_ID, notification("RoxDub", "Мониторинг пайплайна", 0));
        executor = Executors.newSingleThreadScheduledExecutor();
        executor.scheduleAtFixedRate(this::pollStatus, 0, 5, TimeUnit.SECONDS);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if (executor != null) {
            executor.shutdownNow();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void pollStatus() {
        try {
            SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
            String base = prefs.getString(KEY_URL, BuildConfig.DEFAULT_SERVER_URL);
            String token = prefs.getString(KEY_TOKEN, BuildConfig.DEFAULT_TOKEN);
            if (base == null || base.isEmpty()) {
                update("RoxDub", "Нет адреса сервера", 0);
                return;
            }
            if (base.endsWith("/")) {
                base = base.substring(0, base.length() - 1);
            }
            HttpURLConnection connection = (HttpURLConnection) new URL(base + "/status").openConnection();
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(15000);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            String body = read(connection);
            JSONObject json = new JSONObject(body);
            JSONObject progress = json.optJSONObject("progress");
            String state = json.optString("state", "unknown");
            String stage = progress != null ? progress.optString("stage", "Ожидание") : "Ожидание";
            String detail = progress != null ? progress.optString("detail", "") : "";
            int percent = progress != null ? progress.optInt("percent", 0) : 0;
            update("RoxDub: " + percent + "%", state + " · " + stage + (detail.isEmpty() ? "" : " · " + detail), percent);
        } catch (Exception e) {
            update("RoxDub", "Связь потеряна: " + e.getMessage(), 0);
        }
    }

    private String read(HttpURLConnection connection) throws Exception {
        int code = connection.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? connection.getInputStream() : connection.getErrorStream();
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (InputStream input = stream) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
        }
        return output.toString(StandardCharsets.UTF_8.name());
    }

    private void update(String title, String text, int progress) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.notify(NOTIFICATION_ID, notification(title, text, progress));
    }

    private Notification notification(String title, String text, int progress) {
        Intent intent = new Intent(this, MainActivity.class);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this,
                0,
                intent,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return builder
                .setSmallIcon(android.R.drawable.stat_sys_upload_done)
                .setContentTitle(title)
                .setContentText(text)
                .setOngoing(true)
                .setContentIntent(pendingIntent)
                .setProgress(100, Math.max(0, Math.min(100, progress)), false)
                .build();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "RoxDub статус",
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("Фоновый мониторинг пайплайна RoxDub");
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }
}
