package com.mechanicum.roxdub;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.util.Size;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.GridLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final int READ_VIDEO_PERMISSION = 21;
    private static final int NOTIFICATION_PERMISSION = 22;
    private static final int COLOR_BG = 0xFF071014;
    private static final int COLOR_PANEL = 0xFF101D24;
    private static final int COLOR_PANEL_ALT = 0xFF132A33;
    private static final int COLOR_TEXT = 0xFFE7F4F3;
    private static final int COLOR_MUTED = 0xFF9EBABD;
    private static final int COLOR_TEAL = 0xFF00D2C8;
    private static final int COLOR_BLUE = 0xFF1D5BFF;
    private static final int COLOR_GREEN = 0xFF34E89E;
    private static final int COLOR_STEEL = 0xFF6F8D99;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable statusPoller = new Runnable() {
        @Override
        public void run() {
            refreshStatus();
            handler.postDelayed(this, 3000);
        }
    };
    private EditText serverUrl;
    private EditText token;
    private EditText serverVideoPath;
    private TextView selectedPhoneVideo;
    private TextView selectedServerVideo;
    private TextView status;
    private TextView progressText;
    private ProgressBar progressBar;
    private TextView log;
    private GridLayout phoneGrid;
    private GridLayout serverGrid;
    private LinearLayout phraseList;
    private LinearLayout connectionPanel;
    private LinearLayout serverPanel;
    private LinearLayout phonePanel;
    private LinearLayout phrasePanel;
    private LinearLayout statusPanel;
    private CheckBox skipSeparation;
    private Uri selectedPhoneVideoUri;
    private String selectedServerVideoName;
    private MediaPlayer player;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle("RoxDub");
        buildUi();
        showTab(serverPanel);
        loadPhoneVideos();
        refreshServerVideos();
        handler.postDelayed(statusPoller, 3000);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(statusPoller);
        if (player != null) {
            player.release();
            player = null;
        }
        super.onDestroy();
    }

    private void buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(16), dp(16), dp(16));
        root.setBackgroundColor(COLOR_BG);
        scroll.addView(root);

        TextView title = label("RoxDub", COLOR_TEXT, 34);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(title, fullWidth());
        root.addView(label("Пульт Альфа-легиона для дубляжа", COLOR_TEAL, 16), fullWidth());

        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        root.addView(tabs, fullWidth());

        connectionPanel = panel("Связь");
        serverUrl = input("Публичный адрес сервера", BuildConfig.DEFAULT_SERVER_URL);
        token = input("Токен доступа", BuildConfig.DEFAULT_TOKEN);
        connectionPanel.addView(serverUrl);
        connectionPanel.addView(token);
        connectionPanel.addView(button("Проверить сервер", v -> checkServer(), COLOR_BLUE));

        serverPanel = panel("Видео на компьютере");
        serverVideoPath = input("Путь к видео на рабочей машине", "");
        selectedServerVideo = label("Видео из папки не выбрано", COLOR_MUTED, 15);
        serverPanel.addView(selectedServerVideo, fullWidth());
        serverPanel.addView(button("Обновить плитки", v -> refreshServerVideos(), COLOR_BLUE));
        serverGrid = grid();
        serverPanel.addView(serverGrid, fullWidth());
        skipSeparation = new CheckBox(this);
        skipSeparation.setText("Пропустить отделение голоса");
        skipSeparation.setTextColor(COLOR_MUTED);
        skipSeparation.setTextSize(16);
        skipSeparation.setButtonTintList(android.content.res.ColorStateList.valueOf(COLOR_TEAL));
        serverPanel.addView(skipSeparation, fullWidth());
        serverPanel.addView(button("Запустить выбранное", v -> runSelectedServerVideo(), COLOR_GREEN));
        serverPanel.addView(serverVideoPath);
        serverPanel.addView(button("Запустить путь вручную", v -> runServerPath(), COLOR_TEAL));

        phonePanel = panel("Видео на телефоне");
        selectedPhoneVideo = label("Видео с телефона не выбрано", COLOR_MUTED, 15);
        phonePanel.addView(selectedPhoneVideo, fullWidth());
        phonePanel.addView(button("Обновить плитки", v -> loadPhoneVideos(), COLOR_BLUE));
        phoneGrid = grid();
        phonePanel.addView(phoneGrid, fullWidth());
        phonePanel.addView(button("Загрузить выбранное", v -> uploadSelectedOnly(), COLOR_TEAL));
        phonePanel.addView(button("Загрузить и запустить", v -> uploadAndRun(), COLOR_GREEN));

        phrasePanel = panel("Фразы и дорожки");
        phrasePanel.addView(button("Обновить фразы", v -> refreshPhrases(), COLOR_BLUE));
        phraseList = new LinearLayout(this);
        phraseList.setOrientation(LinearLayout.VERTICAL);
        phrasePanel.addView(phraseList, fullWidth());

        statusPanel = panel("Статус");
        statusPanel.addView(button("Обновить", v -> refreshStatus(), COLOR_BLUE));
        statusPanel.addView(button("Фоновый мониторинг", v -> startBackgroundMonitor(), COLOR_TEAL));
        statusPanel.addView(button("Выключить фон", v -> stopBackgroundMonitor(), COLOR_PANEL_ALT));
        statusPanel.addView(button("Остановить", v -> stopJob(), 0xFF7B2030));
        status = label("Состояние: ожидание", COLOR_TEXT, 18);
        statusPanel.addView(status, fullWidth());
        progressText = label("Этап: ожидание", COLOR_TEAL, 17);
        statusPanel.addView(progressText, fullWidth());
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setProgress(0);
        statusPanel.addView(progressBar, fullWidth());
        log = label("", COLOR_MUTED, 13);
        log.setTextIsSelectable(true);
        log.setPadding(dp(12), dp(12), dp(12), dp(12));
        log.setBackground(panelBackground(COLOR_BG, COLOR_STEEL, dp(10)));
        statusPanel.addView(log, fullWidth());

        addTab(tabs, "Комп", serverPanel);
        addTab(tabs, "Телефон", phonePanel);
        addTab(tabs, "Фразы", phrasePanel);
        addTab(tabs, "Статус", statusPanel);
        addTab(tabs, "Связь", connectionPanel);

        root.addView(connectionPanel, fullWidth());
        root.addView(serverPanel, fullWidth());
        root.addView(phonePanel, fullWidth());
        root.addView(phrasePanel, fullWidth());
        root.addView(statusPanel, fullWidth());
        setContentView(scroll);
    }

    private void addTab(LinearLayout tabs, String text, LinearLayout target) {
        Button tab = button(text, v -> showTab(target), COLOR_PANEL_ALT);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
        params.setMargins(dp(3), dp(8), dp(3), dp(8));
        tabs.addView(tab, params);
    }

    private void showTab(LinearLayout visible) {
        for (LinearLayout panel : new LinearLayout[]{connectionPanel, serverPanel, phonePanel, phrasePanel, statusPanel}) {
            if (panel != null) {
                panel.setVisibility(panel == visible ? View.VISIBLE : View.GONE);
            }
        }
    }

    private LinearLayout panel(String title) {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(14), dp(14), dp(14), dp(14));
        panel.setBackground(panelBackground(COLOR_PANEL, COLOR_STEEL, dp(14)));
        TextView label = label(title, COLOR_TEAL, 18);
        label.setTypeface(Typeface.DEFAULT_BOLD);
        panel.addView(label, fullWidth());
        return panel;
    }

    private GridLayout grid() {
        GridLayout grid = new GridLayout(this);
        grid.setColumnCount(2);
        grid.setUseDefaultMargins(true);
        return grid;
    }

    private EditText input(String hint, String value) {
        EditText editText = new EditText(this);
        editText.setHint(hint);
        editText.setText(value);
        editText.setTextColor(COLOR_TEXT);
        editText.setHintTextColor(COLOR_MUTED);
        editText.setTextSize(16);
        editText.setSingleLine(true);
        editText.setBackground(panelBackground(COLOR_PANEL_ALT, COLOR_STEEL, dp(8)));
        editText.setPadding(dp(12), dp(8), dp(12), dp(8));
        editText.setLayoutParams(fullWidth());
        return editText;
    }

    private TextView label(String text, int color, int size) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(color);
        view.setTextSize(size);
        view.setPadding(0, dp(6), 0, dp(6));
        return view;
    }

    private Button button(String text, View.OnClickListener listener, int color) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextColor(COLOR_TEXT);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setBackground(panelBackground(color, COLOR_TEAL, dp(10)));
        button.setOnClickListener(listener);
        return button;
    }

    private View videoTile(String title, String size, Uri localUri, String remoteThumb, View.OnClickListener listener) {
        LinearLayout tile = new LinearLayout(this);
        tile.setOrientation(LinearLayout.VERTICAL);
        tile.setPadding(dp(8), dp(8), dp(8), dp(8));
        tile.setBackground(panelBackground(COLOR_PANEL_ALT, COLOR_STEEL, dp(12)));
        tile.setOnClickListener(listener);

        ImageView image = new ImageView(this);
        image.setBackgroundColor(COLOR_BG);
        image.setScaleType(ImageView.ScaleType.CENTER_CROP);
        tile.addView(image, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(96)));
        if (localUri != null) {
            loadLocalThumbnail(localUri, image);
        } else if (remoteThumb != null) {
            loadRemoteImage(remoteThumb, image);
        }

        TextView name = label(title, COLOR_TEXT, 13);
        name.setMaxLines(2);
        tile.addView(name, fullWidth());
        tile.addView(label(size, COLOR_MUTED, 12), fullWidth());

        GridLayout.LayoutParams params = new GridLayout.LayoutParams();
        params.width = (getResources().getDisplayMetrics().widthPixels - dp(58)) / 2;
        params.setMargins(dp(4), dp(4), dp(4), dp(8));
        tile.setLayoutParams(params);
        return tile;
    }

    private void loadRemoteImage(String path, ImageView image) {
        executor.execute(() -> {
            try {
                String base = baseUrl();
                String url = base + path.replace(" ", "%20") + "?token=" + token.getText().toString().trim();
                Bitmap bitmap = BitmapFactory.decodeStream(new URL(url).openStream());
                runOnUiThread(() -> image.setImageBitmap(bitmap));
            } catch (Exception ignored) {
            }
        });
    }

    private void loadLocalThumbnail(Uri uri, ImageView image) {
        executor.execute(() -> {
            try {
                Bitmap bitmap = getContentResolver().loadThumbnail(uri, new Size(dp(240), dp(140)), null);
                runOnUiThread(() -> image.setImageBitmap(bitmap));
            } catch (Exception ignored) {
            }
        });
    }

    private LinearLayout.LayoutParams fullWidth() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(0, dp(6), 0, dp(6));
        return params;
    }

    private GradientDrawable panelBackground(int color, int stroke, int radius) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(radius);
        drawable.setStroke(dp(1), stroke);
        return drawable;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void loadPhoneVideos() {
        if (checkSelfPermission(Manifest.permission.READ_MEDIA_VIDEO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.READ_MEDIA_VIDEO}, READ_VIDEO_PERMISSION);
            return;
        }
        executor.execute(() -> {
            List<PhoneVideo> videos = new ArrayList<>();
            String[] projection = {
                    MediaStore.Video.Media._ID,
                    MediaStore.Video.Media.DISPLAY_NAME,
                    MediaStore.Video.Media.SIZE
            };
            try (Cursor cursor = getContentResolver().query(
                    MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
                    projection,
                    null,
                    null,
                    MediaStore.Video.Media.DATE_MODIFIED + " DESC"
            )) {
                if (cursor != null) {
                    int idCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media._ID);
                    int nameCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media.DISPLAY_NAME);
                    int sizeCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media.SIZE);
                    while (cursor.moveToNext() && videos.size() < 80) {
                        long id = cursor.getLong(idCol);
                        videos.add(new PhoneVideo(
                                Uri.withAppendedPath(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, String.valueOf(id)),
                                cursor.getString(nameCol),
                                cursor.getLong(sizeCol)
                        ));
                    }
                }
            } catch (Exception e) {
                showStatus("Ошибка чтения видео телефона: " + e.getMessage());
            }
            runOnUiThread(() -> renderPhoneVideos(videos));
        });
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == READ_VIDEO_PERMISSION) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                loadPhoneVideos();
            } else {
                showStatus("Нет доступа к видео на телефоне");
            }
        } else if (requestCode == NOTIFICATION_PERMISSION) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                startBackgroundMonitor();
            } else {
                showStatus("Нет разрешения на уведомления, фоновый статус не включён");
            }
        }
    }

    private void renderPhoneVideos(List<PhoneVideo> videos) {
        phoneGrid.removeAllViews();
        if (videos.isEmpty()) {
            phoneGrid.addView(label("Видео не найдены", COLOR_MUTED, 14));
            return;
        }
        for (PhoneVideo video : videos) {
            phoneGrid.addView(videoTile(video.name, readableSize(video.size), video.uri, null, v -> {
                selectedPhoneVideoUri = video.uri;
                selectedPhoneVideo.setText("Выбрано: " + video.name);
            }));
        }
    }

    private void refreshServerVideos() {
        executor.execute(() -> {
            try {
                JSONArray arr = new JSONObject(request("GET", "/videos", null, true)).getJSONArray("videos");
                List<ServerVideo> videos = new ArrayList<>();
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject item = arr.getJSONObject(i);
                    videos.add(new ServerVideo(item.getString("name"), item.getLong("size"), item.getString("thumbnail_url")));
                }
                runOnUiThread(() -> renderServerVideos(videos));
            } catch (Exception e) {
                showStatus("Ошибка списка компьютера: " + e.getMessage());
            }
        });
    }

    private void renderServerVideos(List<ServerVideo> videos) {
        serverGrid.removeAllViews();
        if (videos.isEmpty()) {
            serverGrid.addView(label("В папке videos пока пусто", COLOR_MUTED, 14));
            return;
        }
        for (ServerVideo video : videos) {
            serverGrid.addView(videoTile(video.name, readableSize(video.size), null, video.thumbnailUrl, v -> {
                selectedServerVideoName = video.name;
                selectedServerVideo.setText("Выбрано: " + video.name);
            }));
        }
    }

    private void refreshPhrases() {
        executor.execute(() -> {
            try {
                JSONArray arr = new JSONObject(request("GET", "/phrases", null, true)).getJSONArray("phrases");
                List<Phrase> phrases = new ArrayList<>();
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject item = arr.getJSONObject(i);
                    phrases.add(new Phrase(
                            item.getInt("index"),
                            item.getString("source_text"),
                            item.getString("translated_text"),
                            item.getString("source_audio_url"),
                            item.getString("translated_audio_url")
                    ));
                }
                runOnUiThread(() -> renderPhrases(phrases));
            } catch (Exception e) {
                showStatus("Ошибка списка фраз: " + e.getMessage());
            }
        });
    }

    private void renderPhrases(List<Phrase> phrases) {
        phraseList.removeAllViews();
        if (phrases.isEmpty()) {
            phraseList.addView(label("Фразы появятся после обработки", COLOR_MUTED, 14));
            return;
        }
        for (Phrase phrase : phrases) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.VERTICAL);
            row.setPadding(dp(10), dp(10), dp(10), dp(10));
            row.setBackground(panelBackground(COLOR_PANEL_ALT, COLOR_STEEL, dp(10)));
            row.addView(label(phrase.index + ". " + phrase.sourceText, COLOR_MUTED, 14), fullWidth());
            row.addView(label(phrase.translatedText, COLOR_TEXT, 15), fullWidth());
            LinearLayout buttons = new LinearLayout(this);
            buttons.setOrientation(LinearLayout.HORIZONTAL);
            buttons.addView(button("Оригинал", v -> playAudio(phrase.sourceAudioUrl), COLOR_BLUE), new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
            buttons.addView(button("Русский", v -> playAudio(phrase.translatedAudioUrl), COLOR_GREEN), new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
            row.addView(buttons, fullWidth());
            phraseList.addView(row, fullWidth());
        }
    }

    private void playAudio(String path) {
        try {
            if (player != null) {
                player.release();
            }
            player = new MediaPlayer();
            player.setDataSource(baseUrl() + path);
            player.setOnPreparedListener(MediaPlayer::start);
            player.prepareAsync();
        } catch (Exception e) {
            showStatus("Ошибка проигрывания: " + e.getMessage());
        }
    }

    private void checkServer() {
        executor.execute(() -> {
            try {
                showStatus("Сервер: " + request("GET", "/health", null, false));
            } catch (Exception e) {
                showStatus("Ошибка сервера: " + e.getMessage());
            }
        });
    }

    private void refreshStatus() {
        executor.execute(() -> {
            try {
                showStatusPayload(request("GET", "/status", null, true));
                showLog(request("GET", "/log", null, true));
                refreshPhrases();
            } catch (Exception e) {
                showStatus("Ошибка статуса: " + e.getMessage());
            }
        });
    }

    private void stopJob() {
        executor.execute(() -> {
            try {
                showStatus(request("POST", "/stop", new byte[0], true));
            } catch (Exception e) {
                showStatus("Ошибка остановки: " + e.getMessage());
            }
        });
    }

    private void runServerPath() {
        executor.execute(() -> {
            try {
                String path = serverVideoPath.getText().toString().trim();
                if (path.isEmpty()) {
                    showToast("Укажи путь к видео");
                    return;
                }
                runPath(path);
                runOnUiThread(() -> showTab(statusPanel));
                refreshStatus();
            } catch (Exception e) {
                showStatus("Ошибка запуска: " + e.getMessage());
            }
        });
    }

    private void runSelectedServerVideo() {
        executor.execute(() -> {
            try {
                if (selectedServerVideoName == null) {
                    showToast("Выбери видео с компьютера");
                    return;
                }
                JSONObject payload = new JSONObject();
                payload.put("name", selectedServerVideoName);
                payload.put("source_lang", "auto");
                payload.put("target_lang", "ru");
                payload.put("skip_separation", skipSeparation.isChecked());
                payload.put("skip_translation", false);
                request("POST", "/run-video", payload.toString().getBytes(StandardCharsets.UTF_8), true);
                runOnUiThread(() -> showTab(statusPanel));
                refreshStatus();
            } catch (Exception e) {
                showStatus("Ошибка запуска выбранного видео: " + e.getMessage());
            }
        });
    }

    private void uploadSelectedOnly() {
        executor.execute(() -> {
            try {
                if (selectedPhoneVideoUri == null) {
                    showToast("Сначала выбери видео");
                    return;
                }
                showStatus("Загрузка...");
                upload(selectedPhoneVideoUri);
                refreshServerVideos();
                showStatus("Загружено в папку videos");
            } catch (Exception e) {
                showStatus("Ошибка загрузки: " + e.getMessage());
            }
        });
    }

    private void uploadAndRun() {
        executor.execute(() -> {
            try {
                if (selectedPhoneVideoUri == null) {
                    showToast("Сначала выбери видео");
                    return;
                }
                showStatus("Загрузка...");
                String uploadedPath = upload(selectedPhoneVideoUri);
                runPath(uploadedPath);
                refreshServerVideos();
                runOnUiThread(() -> showTab(statusPanel));
                refreshStatus();
            } catch (Exception e) {
                showStatus("Ошибка загрузки или запуска: " + e.getMessage());
            }
        });
    }

    private void runPath(String path) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("video_path", path);
        payload.put("source_lang", "auto");
        payload.put("target_lang", "ru");
        payload.put("skip_separation", skipSeparation.isChecked());
        payload.put("skip_translation", false);
        request("POST", "/run", payload.toString().getBytes(StandardCharsets.UTF_8), true);
    }

    private String upload(Uri uri) throws Exception {
        String boundary = "RoxDub-" + UUID.randomUUID();
        HttpURLConnection connection = openConnection("/upload");
        connection.setRequestMethod("POST");
        connection.setDoOutput(true);
        connection.setRequestProperty("Authorization", "Bearer " + token.getText().toString().trim());
        connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);

        String name = displayName(uri);
        try (OutputStream output = connection.getOutputStream();
             InputStream input = new BufferedInputStream(getContentResolver().openInputStream(uri))) {
            output.write(("--" + boundary + "\r\n").getBytes(StandardCharsets.UTF_8));
            output.write(("Content-Disposition: form-data; name=\"file\"; filename=\"" + name + "\"\r\n").getBytes(StandardCharsets.UTF_8));
            output.write("Content-Type: application/octet-stream\r\n\r\n".getBytes(StandardCharsets.UTF_8));
            byte[] buffer = new byte[1024 * 1024];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            output.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
        }
        return new JSONObject(readResponse(connection)).getString("video_path");
    }

    private String displayName(Uri uri) {
        try (Cursor cursor = getContentResolver().query(uri, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) {
                    return cursor.getString(index);
                }
            }
        } catch (Exception ignored) {
        }
        return uri.toString();
    }

    private String request(String method, String path, byte[] body, boolean auth) throws Exception {
        HttpURLConnection connection = openConnection(path);
        connection.setRequestMethod(method);
        connection.setRequestProperty("Accept", "application/json");
        if (auth) {
            connection.setRequestProperty("Authorization", "Bearer " + token.getText().toString().trim());
        }
        if (body != null) {
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json");
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body);
            }
        }
        return readResponse(connection);
    }

    private HttpURLConnection openConnection(String path) throws Exception {
        URL url = new URL(baseUrl() + path);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(60000);
        return connection;
    }

    private String baseUrl() {
        saveConnectionSettings();
        String base = serverUrl.getText().toString().trim();
        if (base.endsWith("/")) {
            return base.substring(0, base.length() - 1);
        }
        return base;
    }

    private String readResponse(HttpURLConnection connection) throws Exception {
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
        String body = output.toString(StandardCharsets.UTF_8.name());
        if (code < 200 || code >= 300) {
            throw new IllegalStateException(code + ": " + body);
        }
        return body;
    }

    private void showStatus(String text) {
        runOnUiThread(() -> status.setText(text));
    }

    private void showStatusPayload(String body) throws Exception {
        JSONObject json = new JSONObject(body);
        JSONObject progress = json.optJSONObject("progress");
        String state = json.optString("state", "unknown");
        String error = json.optString("error", "");
        String stage = progress != null ? progress.optString("stage", "Ожидание") : "Ожидание";
        String detail = progress != null ? progress.optString("detail", "") : "";
        int percent = progress != null ? progress.optInt("percent", 0) : 0;
        runOnUiThread(() -> {
            boolean hasError = error != null && !error.isEmpty() && !"null".equals(error);
            status.setText("Состояние: " + state + (hasError ? "\n" + error : ""));
            progressText.setText("Этап: " + stage + "\n" + percent + "%" + (detail == null || detail.isEmpty() ? "" : " · " + detail));
            progressBar.setProgress(percent);
        });
    }

    private void showLog(String text) {
        runOnUiThread(() -> log.setText(text));
    }

    private void showToast(String text) {
        runOnUiThread(() -> Toast.makeText(this, text, Toast.LENGTH_SHORT).show());
    }

    private void saveConnectionSettings() {
        SharedPreferences prefs = getSharedPreferences(StatusForegroundService.PREFS, MODE_PRIVATE);
        prefs.edit()
                .putString(StatusForegroundService.KEY_URL, serverUrl.getText().toString().trim())
                .putString(StatusForegroundService.KEY_TOKEN, token.getText().toString().trim())
                .apply();
    }

    private void startBackgroundMonitor() {
        saveConnectionSettings();
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATION_PERMISSION);
            return;
        }
        startForegroundService(new Intent(this, StatusForegroundService.class));
        showToast("Фоновый мониторинг включён");
    }

    private void stopBackgroundMonitor() {
        stopService(new Intent(this, StatusForegroundService.class));
        showToast("Фоновый мониторинг выключен");
    }

    private String readableSize(long bytes) {
        double mb = bytes / 1024.0 / 1024.0;
        if (mb < 1024) {
            return String.format(java.util.Locale.US, "%.1f МБ", mb);
        }
        return String.format(java.util.Locale.US, "%.1f ГБ", mb / 1024.0);
    }

    private static class PhoneVideo {
        final Uri uri;
        final String name;
        final long size;

        PhoneVideo(Uri uri, String name, long size) {
            this.uri = uri;
            this.name = name;
            this.size = size;
        }
    }

    private static class ServerVideo {
        final String name;
        final long size;
        final String thumbnailUrl;

        ServerVideo(String name, long size, String thumbnailUrl) {
            this.name = name;
            this.size = size;
            this.thumbnailUrl = thumbnailUrl;
        }
    }

    private static class Phrase {
        final int index;
        final String sourceText;
        final String translatedText;
        final String sourceAudioUrl;
        final String translatedAudioUrl;

        Phrase(int index, String sourceText, String translatedText, String sourceAudioUrl, String translatedAudioUrl) {
            this.index = index;
            this.sourceText = sourceText;
            this.translatedText = translatedText;
            this.sourceAudioUrl = sourceAudioUrl;
            this.translatedAudioUrl = translatedAudioUrl;
        }
    }
}
