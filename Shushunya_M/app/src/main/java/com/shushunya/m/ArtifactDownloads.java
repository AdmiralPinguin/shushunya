package com.shushunya.m;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Durable artifact downloads for chat cards.
 *
 * DownloadManager owns transfer/retry state, so a download continues after the
 * activity or app process disappears. This class only persists the stable
 * artifact -> DownloadManager id binding and refuses to open/share bytes until
 * both the server-declared size and SHA-256 have been verified.
 */
final class ArtifactDownloads implements AutoCloseable {
    private static final String PREFS = "shushunya_artifact_downloads_v1";
    private static final String CLIENT_USER_AGENT = "ShushunyaM/7.3 (Android artifact client)";
    private static final long NO_DOWNLOAD = -1L;
    private static final long POLL_INTERVAL_MS = 750L;

    enum State {
        READY,
        QUEUED,
        DOWNLOADING,
        VERIFYING,
        COMPLETE,
        FAILED
    }

    static final class Artifact {
        final String id;
        final String displayName;
        final String mimeType;
        final long sizeBytes;
        final String sha256;
        final String contentPath;

        private Artifact(
                String id,
                String displayName,
                String mimeType,
                long sizeBytes,
                String sha256,
                String contentPath
        ) {
            this.id = id;
            this.displayName = safeDisplayName(displayName, id);
            this.mimeType = safeMimeType(mimeType);
            this.sizeBytes = sizeBytes;
            this.sha256 = String.valueOf(sha256 == null ? "" : sha256).trim().toLowerCase(Locale.ROOT);
            this.contentPath = safeContentPath(contentPath, id);
        }

        static Artifact fromMessage(JSONObject message) {
            if (message == null) {
                return null;
            }
            JSONObject metadata = message.optJSONObject("artifact");
            String topLevelId = clean(message.optString("artifact_id", ""));
            String nestedId = metadata == null ? "" : clean(metadata.optString("artifact_id", ""));
            if (nestedId.isEmpty() && metadata != null) {
                nestedId = clean(metadata.optString("id", ""));
            }
            String id = topLevelId.isEmpty() ? nestedId : topLevelId;
            if (!topLevelId.isEmpty() && !nestedId.isEmpty() && !topLevelId.equals(nestedId)) {
                // The chat row owns attachment identity. Do not let mismatched
                // decorative metadata redirect the card to another catalog row.
                metadata = null;
            }
            if (id.isEmpty() || "null".equalsIgnoreCase(id)) {
                return null;
            }

            String name = metadata == null ? "" : clean(metadata.optString("name", ""));
            if (name.isEmpty() && metadata != null) {
                name = clean(metadata.optString("display_name", ""));
            }
            if (name.isEmpty() && metadata != null) {
                name = clean(metadata.optString("filename", ""));
            }
            if (name.isEmpty()) {
                name = clean(message.optString("artifact_name", ""));
            }

            String mime = metadata == null ? "" : clean(metadata.optString("mime_type", ""));
            if (mime.isEmpty() && metadata != null) {
                mime = clean(metadata.optString("mime", ""));
            }
            if (mime.isEmpty() && metadata != null) {
                mime = clean(metadata.optString("media_type", ""));
            }
            if (mime.isEmpty()) {
                mime = clean(message.optString("artifact_mime_type", ""));
            }

            long size = -1L;
            if (metadata != null && metadata.has("size_bytes") && !metadata.isNull("size_bytes")) {
                size = metadata.optLong("size_bytes", -1L);
            } else if (metadata != null && metadata.has("bytes") && !metadata.isNull("bytes")) {
                size = metadata.optLong("bytes", -1L);
            } else if (message.has("artifact_size_bytes") && !message.isNull("artifact_size_bytes")) {
                size = message.optLong("artifact_size_bytes", -1L);
            }

            String sha = metadata == null ? "" : clean(metadata.optString("sha256", ""));
            if (sha.isEmpty() && metadata != null) {
                sha = clean(metadata.optString("checksum_sha256", ""));
            }
            if (sha.isEmpty()) {
                sha = clean(message.optString("artifact_sha256", ""));
            }

            String path = metadata == null ? "" : clean(metadata.optString("content_path", ""));
            if (path.isEmpty() && metadata != null) {
                path = clean(metadata.optString("download_path", ""));
            }
            if (path.isEmpty() && metadata != null) {
                path = clean(metadata.optString("content_url", ""));
            }
            if (path.isEmpty()) {
                path = clean(message.optString("artifact_content_path", ""));
            }
            return new Artifact(id, name, mime, size, sha, path);
        }

        String fingerprint() {
            return sha256 + ":" + sizeBytes;
        }

        boolean hasVerificationContract() {
            return sizeBytes >= 0 && sha256.matches("[0-9a-f]{64}");
        }

        private static String safeDisplayName(String value, String id) {
            String clean = clean(value)
                    .replace('\\', '_')
                    .replace('/', '_')
                    .replace('\r', '_')
                    .replace('\n', '_')
                    .replace('\t', '_');
            clean = clean.replaceAll("[\\p{Cntrl}]", "_").trim();
            while (clean.startsWith(".")) {
                clean = clean.substring(1).trim();
            }
            if (clean.isEmpty()) {
                String suffix = safeIdFragment(id);
                clean = suffix.isEmpty() ? "shushunya-file" : "shushunya-file-" + suffix;
            }
            if (clean.length() > 180) {
                int dot = clean.lastIndexOf('.');
                String extension = dot > 0 && clean.length() - dot <= 16 ? clean.substring(dot) : "";
                clean = clean.substring(0, Math.max(1, 180 - extension.length())) + extension;
            }
            return clean;
        }

        private static String safeMimeType(String value) {
            String clean = clean(value).toLowerCase(Locale.ROOT);
            if (!clean.matches("[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+")) {
                return "application/octet-stream";
            }
            return clean;
        }

        private static String safeContentPath(String value, String id) {
            String clean = clean(value);
            String canonical = "/archive/client/artifacts/" + Uri.encode(id) + "/content";
            // Artifact identity comes from the typed chat field, not from a URL
            // supplied in display metadata. Only the exact scoped endpoint is
            // accepted; anything else is normalized to the canonical route.
            return canonical.equals(clean) ? clean : canonical;
        }

        private static String safeIdFragment(String id) {
            String clean = clean(id).replaceAll("[^A-Za-z0-9_-]", "");
            return clean.substring(0, Math.min(clean.length(), 12));
        }
    }

    static final class Snapshot {
        final State state;
        final long downloadedBytes;
        final long totalBytes;
        final String detail;
        final Uri localUri;

        Snapshot(State state, long downloadedBytes, long totalBytes, String detail, Uri localUri) {
            this.state = state;
            this.downloadedBytes = Math.max(0L, downloadedBytes);
            this.totalBytes = totalBytes;
            this.detail = detail == null ? "" : detail;
            this.localUri = localUri;
        }

        boolean isBusy() {
            return state == State.QUEUED || state == State.DOWNLOADING || state == State.VERIFYING;
        }

        int progressPercent() {
            if (totalBytes <= 0L) {
                return 0;
            }
            return (int) Math.max(0L, Math.min(100L, downloadedBytes * 100L / totalBytes));
        }
    }

    interface Listener {
        void onArtifactSnapshot(Artifact artifact, Snapshot snapshot);
    }

    private final Activity activity;
    private final DownloadManager downloadManager;
    private final SharedPreferences preferences;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "artifact-download-state");
        thread.setDaemon(true);
        return thread;
    });
    private final Map<String, Artifact> artifacts = new ConcurrentHashMap<>();
    private final Map<String, CopyOnWriteArrayList<Listener>> listeners = new ConcurrentHashMap<>();
    private final java.util.Set<String> verifying = ConcurrentHashMap.newKeySet();
    private final AtomicBoolean pollScheduled = new AtomicBoolean(false);
    private final String baseUrl;
    private volatile boolean closed;
    private volatile boolean uiActive = true;
    private boolean receiverRegistered;

    private final BroadcastReceiver completionReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (!DownloadManager.ACTION_DOWNLOAD_COMPLETE.equals(intent.getAction())) {
                return;
            }
            long completedId = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, NO_DOWNLOAD);
            if (completedId == NO_DOWNLOAD) {
                return;
            }
            for (Artifact artifact : artifacts.values()) {
                if (storedDownloadId(artifact) == completedId) {
                    refresh(artifact);
                    break;
                }
            }
        }
    };

    ArtifactDownloads(Activity activity, String baseUrl) {
        this.activity = activity;
        this.baseUrl = trimSlash(baseUrl);
        this.downloadManager = (DownloadManager) activity.getSystemService(Context.DOWNLOAD_SERVICE);
        this.preferences = activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        try {
            activity.registerReceiver(
                    completionReceiver,
                    new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE),
                    Context.RECEIVER_NOT_EXPORTED
            );
            receiverRegistered = true;
        } catch (RuntimeException ignored) {
            receiverRegistered = false;
        }
    }

    void observe(Artifact artifact, Listener listener) {
        if (artifact == null || listener == null || closed) {
            return;
        }
        artifacts.put(artifact.id, artifact);
        listeners.computeIfAbsent(artifact.id, ignored -> new CopyOnWriteArrayList<>()).add(listener);
        refresh(artifact);
    }

    void refreshVisible() {
        if (closed || !uiActive) {
            return;
        }
        io.execute(() -> {
            boolean busy = false;
            for (Artifact artifact : artifacts.values()) {
                busy |= refreshNow(artifact);
            }
            if (busy) {
                schedulePoll();
            }
        });
    }

    void clearVisible() {
        listeners.clear();
        artifacts.clear();
    }

    void setUiActive(boolean active) {
        uiActive = active && !closed;
        if (uiActive) {
            refreshVisible();
        }
    }

    void startOrRetry(Artifact artifact) {
        if (artifact == null || closed) {
            return;
        }
        artifacts.put(artifact.id, artifact);
        io.execute(() -> {
            if (downloadManager == null) {
                publish(artifact, new Snapshot(State.FAILED, 0, artifact.sizeBytes, "Системная загрузка недоступна.", null));
                return;
            }

            long existingId = storedDownloadId(artifact);
            if (existingId != NO_DOWNLOAD) {
                DownloadRow existing = query(existingId);
                if (existing != null && (
                        existing.status == DownloadManager.STATUS_PENDING
                                || existing.status == DownloadManager.STATUS_RUNNING
                                || existing.status == DownloadManager.STATUS_PAUSED
                )) {
                    refreshNow(artifact);
                    return;
                }
                if (existing != null && existing.status == DownloadManager.STATUS_SUCCESSFUL) {
                    String fingerprint = artifact.fingerprint();
                    String verificationFailure = preferences.getString(failureKey(artifact), "");
                    // A completed transfer is normally reused and (re)verified. If
                    // its bytes already failed this exact metadata contract, the
                    // visible Retry action must really fetch fresh bytes instead of
                    // redisplaying the same terminal verification error forever.
                    if (!verificationFailure.startsWith(fingerprint + "\n")) {
                        refreshNow(artifact);
                        return;
                    }
                }
                try {
                    downloadManager.remove(existingId);
                } catch (RuntimeException ignored) {
                }
                clearRecord(artifact);
            }

            try {
                DownloadManager.Request request = new DownloadManager.Request(Uri.parse(contentUrl(artifact)))
                        .setTitle(artifact.displayName)
                        .setDescription("Файл от Шушуни")
                        .setMimeType(artifact.mimeType)
                        .setAllowedOverMetered(true)
                        .setAllowedOverRoaming(true)
                        // Keep unverified bytes inside this app's scoped storage.
                        // A public Downloads file or a clickable completion
                        // notification would bypass the SHA/size gate below.
                        .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE)
                        .setDestinationInExternalFilesDir(
                                activity,
                                Environment.DIRECTORY_DOWNLOADS,
                                "Shushunya/" + destinationName(artifact)
                        );
                String key = BuildConfig.CLIENT_API_KEY == null ? "" : BuildConfig.CLIENT_API_KEY.trim();
                if (!key.isEmpty()) {
                    request.addRequestHeader("Authorization", "Bearer " + key);
                    request.addRequestHeader("X-Shushunya-Client-Key", key);
                }
                request.addRequestHeader("User-Agent", CLIENT_USER_AGENT);
                long id = downloadManager.enqueue(request);
                boolean persisted = preferences.edit()
                        .putLong(downloadKey(artifact), id)
                        .remove(verifiedKey(artifact))
                        .remove(failureKey(artifact))
                        .commit();
                if (!persisted) {
                    try {
                        downloadManager.remove(id);
                    } catch (RuntimeException ignored) {
                    }
                    publish(artifact, new Snapshot(
                            State.FAILED,
                            0,
                            artifact.sizeBytes,
                            "Не удалось надёжно сохранить состояние загрузки; передача отменена.",
                            null
                    ));
                    return;
                }
                publish(artifact, new Snapshot(State.QUEUED, 0, artifact.sizeBytes, "Передано системному загрузчику.", null));
                schedulePoll();
            } catch (RuntimeException exc) {
                publish(artifact, new Snapshot(
                        State.FAILED,
                        0,
                        artifact.sizeBytes,
                        "Не удалось начать загрузку: " + safeException(exc),
                        null
                ));
            }
        });
    }

    void open(Artifact artifact) {
        launchWithVerifiedUri(artifact, false);
    }

    void share(Artifact artifact) {
        launchWithVerifiedUri(artifact, true);
    }

    private void launchWithVerifiedUri(Artifact artifact, boolean share) {
        if (artifact == null || closed) {
            return;
        }
        io.execute(() -> {
            long id = storedDownloadId(artifact);
            Uri uri = id == NO_DOWNLOAD ? null : completedUri(id, null);
            boolean verified = artifact.fingerprint().equals(preferences.getString(verifiedKey(artifact), ""));
            if (!verified || uri == null) {
                publish(artifact, new Snapshot(State.FAILED, 0, artifact.sizeBytes, "Файл ещё не прошёл проверку.", null));
                return;
            }
            main.post(() -> {
                try {
                    Intent intent;
                    if (share) {
                        intent = new Intent(Intent.ACTION_SEND)
                                .setType(artifact.mimeType)
                                .putExtra(Intent.EXTRA_STREAM, uri);
                    } else {
                        intent = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, artifact.mimeType);
                    }
                    intent.setClipData(ClipData.newUri(activity.getContentResolver(), artifact.displayName, uri));
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    activity.startActivity(share ? Intent.createChooser(intent, "Отправить файл") : intent);
                } catch (ActivityNotFoundException exc) {
                    Toast.makeText(activity, "Нет приложения для этого типа файла.", Toast.LENGTH_LONG).show();
                } catch (RuntimeException exc) {
                    Toast.makeText(activity, "Файл не открылся: " + safeException(exc), Toast.LENGTH_LONG).show();
                }
            });
        });
    }

    private void refresh(Artifact artifact) {
        if (!closed) {
            io.execute(() -> refreshNow(artifact));
        }
    }

    /** Returns true while this artifact still needs polling. */
    private boolean refreshNow(Artifact artifact) {
        if (closed) {
            return false;
        }
        long id = storedDownloadId(artifact);
        if (id == NO_DOWNLOAD) {
            String failure = preferences.getString(failureKey(artifact), "");
            String fingerprint = artifact.fingerprint();
            if (failure.startsWith(fingerprint + "\n")) {
                publish(artifact, new Snapshot(
                        State.FAILED,
                        0,
                        artifact.sizeBytes,
                        failure.substring(fingerprint.length() + 1),
                        null
                ));
            } else {
                publish(artifact, new Snapshot(State.READY, 0, artifact.sizeBytes, verificationReadiness(artifact), null));
            }
            return false;
        }
        DownloadRow row = query(id);
        if (row == null) {
            clearRecord(artifact);
            publish(artifact, new Snapshot(State.READY, 0, artifact.sizeBytes, "Предыдущая загрузка исчезла; можно скачать снова.", null));
            return false;
        }
        long total = row.totalBytes > 0 ? row.totalBytes : artifact.sizeBytes;
        if (row.status == DownloadManager.STATUS_PENDING) {
            publish(artifact, new Snapshot(State.QUEUED, row.downloadedBytes, total, "Ожидает сети или свободного слота.", null));
            schedulePoll();
            return true;
        }
        if (row.status == DownloadManager.STATUS_RUNNING) {
            publish(artifact, new Snapshot(State.DOWNLOADING, row.downloadedBytes, total, "Загрузка идёт; открыть файл можно будет после проверки.", null));
            schedulePoll();
            return true;
        }
        if (row.status == DownloadManager.STATUS_PAUSED) {
            publish(artifact, new Snapshot(State.QUEUED, row.downloadedBytes, total, pausedReason(row.reason), null));
            schedulePoll();
            return true;
        }
        if (row.status == DownloadManager.STATUS_FAILED) {
            preferences.edit().remove(verifiedKey(artifact)).commit();
            publish(artifact, new Snapshot(State.FAILED, row.downloadedBytes, total, failedReason(row.reason), null));
            return false;
        }
        if (row.status == DownloadManager.STATUS_SUCCESSFUL) {
            Uri uri = completedUri(id, row.localUri);
            String fingerprint = artifact.fingerprint();
            if (fingerprint.equals(preferences.getString(verifiedKey(artifact), "")) && uri != null) {
                publish(artifact, new Snapshot(State.COMPLETE, artifact.sizeBytes, artifact.sizeBytes, "Размер и SHA-256 совпали.", uri));
                return false;
            }
            String failure = preferences.getString(failureKey(artifact), "");
            if (failure.startsWith(fingerprint + "\n")) {
                publish(artifact, new Snapshot(State.FAILED, row.downloadedBytes, total, failure.substring(fingerprint.length() + 1), null));
                return false;
            }
            if (verifying.add(artifact.id)) {
                publish(artifact, new Snapshot(State.VERIFYING, row.downloadedBytes, total, "Проверяю размер и SHA-256.", null));
                verifyCompleted(artifact, uri);
            }
            return false;
        }
        publish(artifact, new Snapshot(State.FAILED, row.downloadedBytes, total, "Неизвестное состояние загрузчика.", null));
        return false;
    }

    private void verifyCompleted(Artifact artifact, Uri uri) {
        String failure = "";
        long observedSize = 0L;
        try {
            if (!artifact.hasVerificationContract()) {
                throw new IllegalStateException("Сервер не передал размер и корректный SHA-256; открытие запрещено.");
            }
            if (uri == null) {
                throw new IllegalStateException("Системный загрузчик не вернул URI файла.");
            }
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = activity.getContentResolver().openInputStream(uri)) {
                if (input == null) {
                    throw new IllegalStateException("Загруженный файл недоступен для чтения.");
                }
                byte[] buffer = new byte[1024 * 1024];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    digest.update(buffer, 0, read);
                    observedSize += read;
                }
            }
            String observedSha = hex(digest.digest());
            if (observedSize != artifact.sizeBytes) {
                throw new IllegalStateException("Размер не совпал: получено " + observedSize + ", ожидалось " + artifact.sizeBytes + ".");
            }
            if (!observedSha.equals(artifact.sha256)) {
                throw new IllegalStateException("SHA-256 не совпал; файл повреждён или подменён.");
            }
            preferences.edit()
                    .putString(verifiedKey(artifact), artifact.fingerprint())
                    .remove(failureKey(artifact))
                    .commit();
            publish(artifact, new Snapshot(State.COMPLETE, observedSize, observedSize, "Размер и SHA-256 совпали.", uri));
        } catch (Exception exc) {
            failure = "Проверка файла провалена: " + safeException(exc);
            long failedDownloadId = storedDownloadId(artifact);
            if (downloadManager != null && failedDownloadId != NO_DOWNLOAD) {
                try {
                    downloadManager.remove(failedDownloadId);
                } catch (RuntimeException ignored) {
                }
            }
            preferences.edit()
                    .remove(downloadKey(artifact))
                    .remove(verifiedKey(artifact))
                    .putString(failureKey(artifact), artifact.fingerprint() + "\n" + failure)
                    .commit();
            publish(artifact, new Snapshot(State.FAILED, observedSize, artifact.sizeBytes, failure, null));
        } finally {
            verifying.remove(artifact.id);
        }
    }

    private DownloadRow query(long id) {
        if (downloadManager == null || id == NO_DOWNLOAD) {
            return null;
        }
        try (Cursor cursor = downloadManager.query(new DownloadManager.Query().setFilterById(id))) {
            if (cursor == null || !cursor.moveToFirst()) {
                return null;
            }
            int status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
            int reason = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON));
            long downloaded = cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR));
            long total = cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES));
            String local = cursor.getString(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_URI));
            return new DownloadRow(status, reason, downloaded, total, parseUri(local));
        } catch (RuntimeException exc) {
            return null;
        }
    }

    private Uri completedUri(long id, Uri fallback) {
        if (downloadManager != null) {
            try {
                Uri uri = downloadManager.getUriForDownloadedFile(id);
                if (uri != null) {
                    return uri;
                }
            } catch (RuntimeException ignored) {
            }
        }
        return fallback != null && "content".equalsIgnoreCase(fallback.getScheme()) ? fallback : null;
    }

    private void schedulePoll() {
        if (closed || !uiActive || !pollScheduled.compareAndSet(false, true)) {
            return;
        }
        main.postDelayed(() -> {
            pollScheduled.set(false);
            if (uiActive) {
                refreshVisible();
            }
        }, POLL_INTERVAL_MS);
    }

    private void publish(Artifact artifact, Snapshot snapshot) {
        main.post(() -> {
            if (closed || !uiActive) {
                return;
            }
            CopyOnWriteArrayList<Listener> artifactListeners = listeners.get(artifact.id);
            if (artifactListeners != null) {
                for (Listener listener : artifactListeners) {
                    listener.onArtifactSnapshot(artifact, snapshot);
                }
            }
        });
    }

    private long storedDownloadId(Artifact artifact) {
        return preferences.getLong(downloadKey(artifact), NO_DOWNLOAD);
    }

    private void clearRecord(Artifact artifact) {
        preferences.edit()
                .remove(downloadKey(artifact))
                .remove(verifiedKey(artifact))
                .remove(failureKey(artifact))
                .commit();
    }

    private String contentUrl(Artifact artifact) {
        return baseUrl + artifact.contentPath;
    }

    private String destinationName(Artifact artifact) {
        String prefix = Artifact.safeIdFragment(artifact.id);
        if (prefix.isEmpty()) {
            prefix = shortHash(artifact.id);
        }
        // Each actual transfer gets its own scoped staging name. If Android is
        // still deleting a failed DownloadManager row, an immediate Retry cannot
        // collide with the old partial file.
        String attempt = java.util.UUID.randomUUID().toString().substring(0, 8);
        return prefix + "-" + attempt + "-" + boundedDiskName(artifact.displayName);
    }

    private static String boundedDiskName(String value) {
        String clean = Artifact.safeDisplayName(value, "");
        int dot = clean.lastIndexOf('.');
        String extension = dot > 0 ? clean.substring(dot) : "";
        if (extension.getBytes(StandardCharsets.UTF_8).length > 24) {
            extension = "";
        }
        String stem = extension.isEmpty() ? clean : clean.substring(0, dot);
        int budget = 190 - extension.getBytes(StandardCharsets.UTF_8).length;
        StringBuilder bounded = new StringBuilder();
        int used = 0;
        for (int offset = 0; offset < stem.length();) {
            int codePoint = stem.codePointAt(offset);
            String piece = new String(Character.toChars(codePoint));
            int bytes = piece.getBytes(StandardCharsets.UTF_8).length;
            if (used + bytes > budget) {
                break;
            }
            bounded.append(piece);
            used += bytes;
            offset += Character.charCount(codePoint);
        }
        if (bounded.length() == 0) {
            bounded.append("file");
        }
        return bounded + extension;
    }

    private String downloadKey(Artifact artifact) {
        return "download." + keyToken(artifact.id);
    }

    private String verifiedKey(Artifact artifact) {
        return "verified." + keyToken(artifact.id);
    }

    private String failureKey(Artifact artifact) {
        return "failure." + keyToken(artifact.id);
    }

    private static String keyToken(String id) {
        return shortHash(id) + "." + Artifact.safeIdFragment(id);
    }

    private static String verificationReadiness(Artifact artifact) {
        return artifact.hasVerificationContract()
                ? "Готов к загрузке; после скачивания будут проверены размер и SHA-256."
                : "В метаданных нет размера или SHA-256; открытие будет запрещено.";
    }

    private static String pausedReason(int reason) {
        if (reason == DownloadManager.PAUSED_WAITING_FOR_NETWORK) {
            return "Жду сеть; загрузчик продолжит автоматически.";
        }
        if (reason == DownloadManager.PAUSED_WAITING_TO_RETRY) {
            return "Временная ошибка; системный загрузчик повторит сам.";
        }
        if (reason == DownloadManager.PAUSED_QUEUED_FOR_WIFI) {
            return "Загрузка поставлена в очередь до подходящей сети.";
        }
        return "Загрузка временно приостановлена системой.";
    }

    private static String failedReason(int reason) {
        switch (reason) {
            case DownloadManager.ERROR_CANNOT_RESUME:
                return "Сервер не позволил продолжить загрузку; нажми «Повторить».";
            case DownloadManager.ERROR_DEVICE_NOT_FOUND:
                return "Хранилище устройства недоступно.";
            case DownloadManager.ERROR_FILE_ALREADY_EXISTS:
                return "Файл с таким назначением уже существует; нажми «Повторить».";
            case DownloadManager.ERROR_INSUFFICIENT_SPACE:
                return "На устройстве недостаточно места.";
            case DownloadManager.ERROR_HTTP_DATA_ERROR:
                return "Соединение оборвалось на передаче данных; нажми «Повторить».";
            case DownloadManager.ERROR_TOO_MANY_REDIRECTS:
                return "Сервер вернул слишком много перенаправлений.";
            case DownloadManager.ERROR_UNHANDLED_HTTP_CODE:
                return "Сервер отклонил загрузку; нажми «Повторить».";
            case DownloadManager.ERROR_UNKNOWN:
            default:
                return "Системный загрузчик завершился ошибкой " + reason + "; нажми «Повторить».";
        }
    }

    private static String safeException(Throwable exc) {
        String message = exc == null ? "неизвестная ошибка" : clean(exc.getMessage());
        return message.isEmpty() ? exc.getClass().getSimpleName() : message;
    }

    private static String clean(String value) {
        return String.valueOf(value == null ? "" : value).trim();
    }

    private static String trimSlash(String value) {
        String clean = clean(value);
        while (clean.endsWith("/")) {
            clean = clean.substring(0, clean.length() - 1);
        }
        return clean;
    }

    private static Uri parseUri(String value) {
        try {
            return clean(value).isEmpty() ? null : Uri.parse(value);
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static String hex(byte[] value) {
        StringBuilder result = new StringBuilder(value.length * 2);
        for (byte item : value) {
            result.append(String.format(Locale.ROOT, "%02x", item & 0xff));
        }
        return result.toString();
    }

    private static String shortHash(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            String full = hex(digest.digest(clean(value).getBytes(StandardCharsets.UTF_8)));
            return full.substring(0, 12);
        } catch (Exception ignored) {
            return Integer.toHexString(clean(value).hashCode());
        }
    }

    @Override
    public void close() {
        closed = true;
        main.removeCallbacksAndMessages(null);
        if (receiverRegistered) {
            try {
                activity.unregisterReceiver(completionReceiver);
            } catch (RuntimeException ignored) {
            }
            receiverRegistered = false;
        }
        listeners.clear();
        artifacts.clear();
        verifying.clear();
        // Do not interrupt an in-flight SHA-256 pass merely because the
        // Activity is being recreated. It may finish silently and persist the
        // verified fingerprint; a later Activity will observe that result.
        io.shutdown();
    }

    private static final class DownloadRow {
        final int status;
        final int reason;
        final long downloadedBytes;
        final long totalBytes;
        final Uri localUri;

        DownloadRow(int status, int reason, long downloadedBytes, long totalBytes, Uri localUri) {
            this.status = status;
            this.reason = reason;
            this.downloadedBytes = downloadedBytes;
            this.totalBytes = totalBytes;
            this.localUri = localUri;
        }
    }
}
