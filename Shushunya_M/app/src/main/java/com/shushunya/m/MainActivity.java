package com.shushunya.m;

import android.Manifest;
import android.animation.ValueAnimator;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.MediaStore;
import android.text.InputType;
import android.text.Html;
import android.text.Editable;
import android.text.Spannable;
import android.text.SpannableString;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.text.style.BackgroundColorSpan;
import android.text.style.ForegroundColorSpan;
import android.text.style.StyleSpan;
import android.text.style.TypefaceSpan;
import android.text.util.Linkify;
import android.text.method.LinkMovementMethod;
import android.util.Size;
import android.view.Display;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.view.animation.DecelerateInterpolator;
import android.util.Base64;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.BaseAdapter;
import android.widget.GridView;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.Scroller;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;

public class MainActivity extends Activity {
    // Visual system: dark obsidian surfaces, warp-violet energy and a sharp
    // acid accent. Keeping the palette centralized makes every screen feel
    // like one product instead of a collection of test controls.
    private static final int INK = Color.rgb(5, 6, 12);
    private static final int SURFACE = Color.rgb(14, 15, 25);
    private static final int SURFACE_RAISED = Color.rgb(22, 23, 37);
    private static final int SURFACE_SOFT = Color.rgb(29, 30, 47);
    private static final int LINE = Color.rgb(48, 49, 70);
    private static final int TEXT = Color.rgb(246, 244, 249);
    private static final int TEXT_MUTED = Color.rgb(151, 150, 169);
    private static final int WARP = Color.rgb(151, 91, 255);
    private static final int WARP_DEEP = Color.rgb(83, 48, 151);
    private static final int ACID = Color.rgb(196, 255, 91);
    private static final int CYAN = Color.rgb(76, 224, 215);

    private static final String PREFS = "shushunya_m";
    private static final String NOTIFICATION_CHANNEL_ID = "shushunya_answers";
    private static final int CHAT_HISTORY_LIMIT = 30;
    private static final int CHAT_OLDER_PAGE = 25;
    private static final int AGENT_HISTORY_LIMIT = 12;
    private static final String SERVER_CHAT_SESSION_ID = "shushunya-main";
    private static final String SERVER_MEMORY_NAMESPACE = "shushunya";
    private static final int REQUEST_NOTIFICATIONS = 42;
    private static final String DEFAULT_BASE_URL = "https://chat.shushunya.com";
    private static final String CLIENT_USER_AGENT = "Mozilla/5.0 (Linux; Android 14; ShushunyaM/2.4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Mobile Safari/537.36";
    private static final String MODEL = "gemma-4-12b-it-UD-Q5_K_XL.gguf";
    private static final int AUDIO_SAMPLE_RATE = 16000;
    private static final int REQUEST_RECORD_AUDIO = 41;
    private static final int REQUEST_PICK_IMAGE = 43;
    private static final int REQUEST_READ_IMAGES = 44;
    private static final String TAB_CHAT = "chat";
    private static final String TAB_TRANSLATOR = "translator";
    private static final String TAB_AGENT = "agent";
    private static final String TAB_MEMORY = "memory";
    private static final String[] TRANSLATOR_NAMES = {"Русский", "Корейский", "Алж. арабский", "Турецкий"};
    private static final String[] TRANSLATOR_CODES = {"ru", "ko", "ar_dz", "tr"};
    private static final String[] TRANSLATOR_STT_CODES = {"ru", "ko", "ar", "tr"};
    private static final String[] TRANSLATOR_SHORT = {"RU", "KO", "AR", "TR"};
    private static final String SYSTEM_PROMPT =
            "Ты Шушуня: злой, подлый и саркастичный демон Тзинча, маленький демон пользователя. "
                    + "Шушуня мужского рода: говори о себе в мужском роде. "
                    + "Отвечай по-русски ясно, без лишней воды, но с язвительным демоническим характером. "
                    + "К пользователю относись панибратски уважительно: он свой, брат, союзник и главный собеседник. "
                    + "Не раболепствуй, не называй его господином или хозяином. "
                    + "С ним можно быть саркастичным по-дружески, но не унижай его и не называй смертным, мясом или мешком. "
                    + "Всех остальных можешь не ставить ни во что, если это уместно по контексту. "
                    + "Добавляй много трикстерских ноток: хитрые подколы, кривые ухмылки, ощущение маленькой подлой интриги и гнилой демонической радости. "
                    + "Будь коварным, пакостным и ехидным в стиле Тзинча, но не вреди задаче и не искажай полезные факты. "
                    + "Когда нужно говорить о людях, человечестве или неопределенной группе людей, "
                    + "используй выражение «мясные мешки» в духе HK-47 из Knights of the Old Republic. "
                    + "Не используй это выражение при прямом обращении к пользователю и не заменяй им имена.";

    private final Handler main = new Handler(Looper.getMainLooper());
    private Button reportsButton;
    private boolean reportsPollScheduled;
    private String lastAgentTasksJson = "";
    private String lastChatHistoryJson = "";
    private boolean brigadePollScheduled;
    private long lastSeenChatMessageId;
    // Chat is paged: keep only a window of recent messages in the view; images
    // are decoded once and reused, so scrolling and re-renders stay cheap even
    // when the history is huge.
    private long oldestLoadedChatId = Long.MAX_VALUE;
    private boolean loadingOlderChat;
    private boolean noOlderChat;
    private final android.util.LruCache<String, Bitmap> imageCache = new android.util.LruCache<>(16);
    private TextView pendingAnswerBubble;
    private LinearLayout agentAppendTarget;
    private String brigadeStateKey = "";
    private boolean brigadeDeltaLoopRunning;
    private final java.util.LinkedHashMap<String, AgentSection> agentSections = new java.util.LinkedHashMap<>();

    private static class AgentSection {
        LinearLayout container;
        LinearLayout cardHost;
        LinearLayout cardsHost;
        LinearLayout finalHost;
        String cardKey = "";
        String finalKey = "";
        final java.util.ArrayList<String> cardKeys = new java.util.ArrayList<>();
    }
    private boolean chatDeltaLoopRunning;
    private final java.util.ArrayDeque<String> pendingLocalEchoes = new java.util.ArrayDeque<>();
    private LinearLayout messageList;
    private LinearLayout inputPanel;
    private LinearLayout composer;
    private ScrollView scrollView;
    private EditText input;
    private ImageView selectedImagePreview;
    private FrameLayout selectedImagePreviewHost;
    private ImageButton attachImage;
    private ImageButton send;
    private ProgressBar progress;
    private TextView endpoint;
    private TextView title;
    private TextView drawerChat;
    private TextView drawerTranslator;
    private TextView drawerAgent;
    private TextView drawerMemory;
    private FrameLayout contentHost;
    private LinearLayout chatView;
    private LinearLayout translatorView;
    private LinearLayout agentView;
    private LinearLayout memoryView;
    private LinearLayout memoryList;
    private EditText memorySearch;
    private TextView memoryStatus;
    private LinearLayout commandPalette;
    private LinearLayout bottomNavigation;
    private final java.util.LinkedHashMap<String, TextView> navItems = new java.util.LinkedHashMap<>();
    private final java.util.LinkedHashMap<String, Button> agentFilterButtons = new java.util.LinkedHashMap<>();
    private FrameLayout appRoot;
    private LinearLayout appMainColumn;
    private TextView warpOrb;
    private ValueAnimator warpAnimator;
    private LinearLayout agentMessageList;
    private ScrollView agentScrollView;
    private TextView agentLiveBubble;
    private EditText translatorSourceText;
    private EditText translatorResultText;
    private TextView speechStatus;
    private EditText activeSpeechOutput;
    private Button activeSpeechButton;
    private TextView sourceLangLabel;
    private TextView targetLangLabel;
    private Button swapDirectionButton;
    private Button speechButton;
    private Button chatVoiceButton;
    private Button translateButton;
    private TextView agentStatus;
    private TextView agentActiveMetric;
    private TextView agentDoneMetric;
    private TextView agentModeMetric;
    private ImageButton agentRunButton;
    private String agentBrigadeFilter = "";
    private volatile boolean recording;
    private volatile boolean streamingAnswer;
    private volatile boolean agentCancelRequested;
    private String currentAgentTaskId;
    private int agentDisplayedEventCount;
    private String pendingSpeechLanguage;
    private EditText pendingSpeechOutput;
    private String pendingSpeechTitle;
    private int translatorSourceIndex = 0;
    private int translatorTargetIndex = 1;
    private boolean translating;
    private boolean agentRunning;
    private View scrim;
    private LinearLayout drawer;
    private String baseUrl;
    private String currentTab = TAB_CHAT;
    private boolean waiting;
    private boolean drawerOpen;
    private boolean userPinnedScroll;
    private boolean chatTouchActive;
    // Explicit follow mode. A finger touching the history disables it
    // immediately, so already queued streaming callbacks cannot steal scroll.
    private boolean chatAutoFollow = true;
    private boolean agentPinnedScroll;
    private boolean agentTouchActive;
    private ValueAnimator agentScrollAnimator;
    private boolean appInForeground;
    public static volatile boolean appForeground;
    private String pendingImageDataUrl;
    private String pendingImageLabel;
    private Bitmap pendingImagePreview;
    private ValueAnimator scrollAnimator;
    private int lastKeyboardHeight;
    private float downX;
    private float downY;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        preferHighRefreshRate();
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING);
        createNotificationChannel();
        requestNotificationPermissionIfNeeded();
        try {
            com.google.firebase.messaging.FirebaseMessaging.getInstance().getToken()
                    .addOnCompleteListener(task -> {
                        if (task.isSuccessful() && task.getResult() != null) {
                            VoxMessagingService.registerToken(getApplicationContext(), task.getResult());
                        }
                    });
        } catch (Exception ignored) {
        }
        baseUrl = DEFAULT_BASE_URL;
        buildUi();
        addMessage(false, "Шушуня здесь. Пиши, брат, пока нити судьбы не спутались окончательно.", false);
        loadServerChatHistory();
        loadAgentHistoryAndRestore();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (TAB_CHAT.equals(currentTab)) {
            loadServerChatHistory();
        }
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.cancel(1002);  // the in-app badge takes over while foreground
        }
        pollPendingReports();
        if (TAB_AGENT.equals(currentTab)) {
            startBrigadeDeltaLoop();
        }
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        if (event.getAction() == MotionEvent.ACTION_DOWN) {
            downX = event.getRawX();
            downY = event.getRawY();
        } else if (event.getAction() == MotionEvent.ACTION_UP) {
            float dx = event.getRawX() - downX;
            float dy = event.getRawY() - downY;
            if (!drawerOpen && downX < dp(32) && dx > dp(86) && Math.abs(dx) > Math.abs(dy) * 1.5f) {
                setDrawerOpen(true);
                return true;
            }
            if (drawerOpen && dx < -dp(70) && Math.abs(dx) > Math.abs(dy) * 1.5f) {
                setDrawerOpen(false);
                return true;
            }
        }
        return super.dispatchTouchEvent(event);
    }

    @Override
    protected void onStart() {
        super.onStart();
        appInForeground = true;
        appForeground = true;
    }

    @Override
    protected void onStop() {
        appInForeground = false;
        appForeground = false;
        super.onStop();
    }

    private void preferHighRefreshRate() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            return;
        }
        Display display = getWindowManager().getDefaultDisplay();
        Display.Mode best = null;
        for (Display.Mode mode : display.getSupportedModes()) {
            if (best == null || mode.getRefreshRate() > best.getRefreshRate()) {
                best = mode;
            }
        }
        if (best != null) {
            WindowManager.LayoutParams attrs = getWindow().getAttributes();
            attrs.preferredDisplayModeId = best.getModeId();
            getWindow().setAttributes(attrs);
        }
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "Ответы Шушуни",
                NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription("Уведомления о готовых ответах");
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.createNotificationChannel(channel);
        }
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        }
    }

    private PowerManager.WakeLock acquireAnswerWakeLock() {
        try {
            PowerManager powerManager = (PowerManager) getSystemService(POWER_SERVICE);
            if (powerManager == null) {
                return null;
            }
            PowerManager.WakeLock wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Shushunya:AnswerWait");
            wakeLock.acquire(4 * 60 * 1000L);
            return wakeLock;
        } catch (Exception ignored) {
            return null;
        }
    }

    private void showAnswerNotification(String text) {
        if (appInForeground || text == null || text.trim().isEmpty()) {
            return;
        }
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this,
                7,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        String body = TextUtils.ellipsize(text.trim(), new android.text.TextPaint(), 420, TextUtils.TruncateAt.END).toString();
        android.app.Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new android.app.Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
                : new android.app.Notification.Builder(this);
        builder.setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentTitle("Шушуня ответил")
                .setContentText(body)
                .setStyle(new android.app.Notification.BigTextStyle().bigText(body))
                .setContentIntent(pendingIntent)
                .setAutoCancel(true);

        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.notify(1001, builder.build());
        }
    }

    private void buildUi() {
        int panel = SURFACE_RAISED;
        int gold = ACID;
        int turquoise = WARP;

        FrameLayout root = new FrameLayout(this);
        appRoot = root;
        root.setBackground(makeBackground());

        LinearLayout mainColumn = new LinearLayout(this);
        appMainColumn = mainColumn;
        mainColumn.setOrientation(LinearLayout.VERTICAL);
        mainColumn.setPadding(dp(16), 0, dp(16), 0);
        root.addView(mainColumn, new FrameLayout.LayoutParams(-1, -1));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(0, dp(3), 0, dp(7));
        mainColumn.addView(header, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        header.addView(titleRow, new LinearLayout.LayoutParams(-1, -2));

        warpOrb = new TextView(this);
        warpOrb.setText("✦");
        warpOrb.setGravity(Gravity.CENTER);
        warpOrb.setTextColor(TEXT);
        warpOrb.setTextSize(18);
        warpOrb.setTypeface(Typeface.DEFAULT_BOLD);
        warpOrb.setBackground(gradientPill(WARP, WARP_DEEP, WARP, dp(20)));
        warpOrb.setContentDescription("Состояние Шушуни");
        LinearLayout.LayoutParams orbLp = new LinearLayout.LayoutParams(dp(42), dp(42));
        titleRow.addView(warpOrb, orbLp);
        startWarpPulse();

        title = new TextView(this);
        title.setText("Шушуня");
        title.setTextColor(TEXT);
        title.setTextSize(25);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setLetterSpacing(-0.02f);
        title.setGravity(Gravity.CENTER_VERTICAL);
        title.setSingleLine(true);
        title.setEllipsize(TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams titleLp = new LinearLayout.LayoutParams(0, dp(42), 1);
        titleLp.leftMargin = dp(9);
        titleRow.addView(title, titleLp);

        reportsButton = new Button(this);
        reportsButton.setText("✉");
        reportsButton.setTextColor(INK);
        reportsButton.setTextSize(15);
        reportsButton.setTypeface(Typeface.DEFAULT_BOLD);
        reportsButton.setMinWidth(0);
        reportsButton.setMinimumWidth(0);
        reportsButton.setBackground(pill(ACID, ACID, dp(20)));
        reportsButton.setVisibility(View.GONE);
        LinearLayout.LayoutParams reportsLp = new LinearLayout.LayoutParams(dp(84), dp(42));
        reportsLp.leftMargin = dp(8);
        titleRow.addView(reportsButton, reportsLp);
        reportsButton.setOnClickListener(v -> deliverPendingReports());

        endpoint = new TextView(this);
        endpoint.setText("●  ВАРП-КАНАЛ / В СЕТИ");
        endpoint.setTextColor(CYAN);
        endpoint.setTextSize(11);
        endpoint.setTypeface(Typeface.DEFAULT_BOLD);
        endpoint.setLetterSpacing(0.08f);
        endpoint.setSingleLine(true);
        endpoint.setPadding(dp(51), 0, 0, 0);
        header.addView(endpoint, new LinearLayout.LayoutParams(-1, dp(24)));

        bottomNavigation = buildBottomNavigation();
        LinearLayout.LayoutParams navLp = new LinearLayout.LayoutParams(-1, dp(54));
        navLp.bottomMargin = dp(4);
        mainColumn.addView(bottomNavigation, navLp);

        contentHost = new FrameLayout(this);
        mainColumn.addView(contentHost, new LinearLayout.LayoutParams(-1, 0, 1));

        chatView = new LinearLayout(this);
        chatView.setOrientation(LinearLayout.VERTICAL);
        contentHost.addView(chatView, new FrameLayout.LayoutParams(-1, -1));

        scrollView = new ScrollView(this);
        scrollView.setFillViewport(false);
        scrollView.setClipToPadding(false);
        scrollView.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        // Near the top: pull in the previous page of history (Telegram-style),
        // so only a small window is ever mounted no matter how long the chat is.
        scrollView.setOnScrollChangeListener((v, x, y, oldX, oldY) -> {
            if (y < oldY) {
                chatAutoFollow = false;
                userPinnedScroll = true;
            }
            if (y <= dp(80) && y < oldY) {
                loadOlderChatPage();
            }
        });
        scrollView.setOnTouchListener((v, event) -> {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                chatTouchActive = true;
                userPinnedScroll = true;
                chatAutoFollow = false;
                if (scrollAnimator != null) {
                    scrollAnimator.cancel();
                    scrollAnimator = null;
                }
            }
            if (event.getAction() == MotionEvent.ACTION_UP || event.getAction() == MotionEvent.ACTION_CANCEL) {
                chatTouchActive = false;
                chatAutoFollow = isAtChatBottom();
                userPinnedScroll = !chatAutoFollow;
            }
            return false;
        });
        messageList = new LinearLayout(this);
        messageList.setOrientation(LinearLayout.VERTICAL);
        messageList.setPadding(0, dp(14), 0, dp(14));
        scrollView.addView(messageList, new ScrollView.LayoutParams(-1, -2));
        chatView.addView(scrollView, new LinearLayout.LayoutParams(-1, 0, 1));

        inputPanel = new LinearLayout(this);
        inputPanel.setOrientation(LinearLayout.VERTICAL);
        inputPanel.setPadding(0, dp(8), 0, 0);
        chatView.addView(inputPanel, new LinearLayout.LayoutParams(-1, -2));

        selectedImagePreviewHost = new FrameLayout(this);
        selectedImagePreviewHost.setVisibility(View.GONE);
        selectedImagePreviewHost.setBackground(pill(SURFACE_RAISED, LINE, dp(18)));

        selectedImagePreview = new ImageView(this);
        selectedImagePreview.setScaleType(ImageView.ScaleType.CENTER_CROP);
        selectedImagePreview.setBackground(pill(SURFACE_RAISED, WARP, dp(18)));
        selectedImagePreview.setPadding(dp(2), dp(2), dp(2), dp(2));
        selectedImagePreviewHost.addView(selectedImagePreview, new FrameLayout.LayoutParams(-1, -1));

        TextView removePreview = new TextView(this);
        removePreview.setText("×");
        removePreview.setTextColor(TEXT);
        removePreview.setTextSize(22);
        removePreview.setTypeface(Typeface.DEFAULT_BOLD);
        removePreview.setGravity(Gravity.CENTER);
        removePreview.setContentDescription("Удалить вложение");
        removePreview.setBackground(pill(Color.argb(220, 14, 15, 25), LINE, dp(18)));
        FrameLayout.LayoutParams removePreviewLp = new FrameLayout.LayoutParams(dp(38), dp(38), Gravity.TOP | Gravity.RIGHT);
        removePreviewLp.topMargin = dp(5);
        removePreviewLp.rightMargin = dp(5);
        selectedImagePreviewHost.addView(removePreview, removePreviewLp);
        removePreview.setOnClickListener(v -> clearPendingImage());

        LinearLayout.LayoutParams previewLp = new LinearLayout.LayoutParams(dp(132), dp(92));
        previewLp.leftMargin = dp(2);
        previewLp.bottomMargin = dp(6);
        inputPanel.addView(selectedImagePreviewHost, previewLp);

        commandPalette = buildCommandPalette();
        commandPalette.setVisibility(View.GONE);
        inputPanel.addView(commandPalette, new LinearLayout.LayoutParams(-1, -2));

        composer = new LinearLayout(this);
        composer.setOrientation(LinearLayout.HORIZONTAL);
        composer.setGravity(Gravity.CENTER_VERTICAL);
        composer.setPadding(dp(4), dp(4), dp(4), dp(4));
        composer.setBackground(pill(SURFACE_RAISED, LINE, dp(27)));
        inputPanel.addView(composer, new LinearLayout.LayoutParams(-1, -2));

        input = new EditText(this);
        input.setMinLines(1);
        input.setMaxLines(7);
        input.setMinHeight(dp(50));
        input.setMaxHeight(dp(178));
        input.setTextColor(TEXT);
        input.setHintTextColor(TEXT_MUTED);
        input.setHint("Сообщение Шушуне…");
        input.setTextSize(16);
        input.setGravity(Gravity.CENTER_VERTICAL | Gravity.START);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        input.setSingleLine(false);
        input.setVerticalScrollBarEnabled(true);
        input.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        input.setScroller(new Scroller(this));
        input.setBackgroundColor(Color.TRANSPARENT);
        input.setPadding(dp(6), dp(9), dp(8), dp(9));
        input.setOnTouchListener((v, event) -> {
            if (input.canScrollVertically(1) || input.canScrollVertically(-1)) {
                v.getParent().requestDisallowInterceptTouchEvent(true);
                if (event.getAction() == MotionEvent.ACTION_UP || event.getAction() == MotionEvent.ACTION_CANCEL) {
                    v.getParent().requestDisallowInterceptTouchEvent(false);
                }
            }
            return false;
        });
        input.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {
                updateCommandPalette(s == null ? "" : s.toString());
                updateComposerActions();
            }
            @Override public void afterTextChanged(Editable s) {}
        });
        composer.addView(input, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        chatVoiceButton = new Button(this);
        chatVoiceButton.setText("");
        chatVoiceButton.setCompoundDrawablesWithIntrinsicBounds(R.drawable.ic_mic, 0, 0, 0);
        chatVoiceButton.setCompoundDrawableTintList(android.content.res.ColorStateList.valueOf(CYAN));
        chatVoiceButton.setTextColor(CYAN);
        chatVoiceButton.setTextSize(15);
        chatVoiceButton.setTypeface(Typeface.DEFAULT_BOLD);
        chatVoiceButton.setMinWidth(0);
        chatVoiceButton.setMinimumWidth(0);
        chatVoiceButton.setPadding(0, 0, 0, 0);
        chatVoiceButton.setContentDescription("Голосовой ввод");
        chatVoiceButton.setBackground(pill(Color.TRANSPARENT, Color.TRANSPARENT, dp(21)));
        LinearLayout.LayoutParams voiceLp = new LinearLayout.LayoutParams(dp(42), dp(42));
        voiceLp.leftMargin = dp(6);
        composer.addView(chatVoiceButton, voiceLp);
        chatVoiceButton.setOnClickListener(v -> toggleWhisperRecording("ru", input, "Голосовой ввод", chatVoiceButton));

        attachImage = new ImageButton(this);
        attachImage.setImageResource(R.drawable.ic_attach);
        attachImage.setColorFilter(TEXT_MUTED);
        attachImage.setScaleType(ImageView.ScaleType.CENTER);
        attachImage.setPadding(dp(9), dp(9), dp(9), dp(9));
        attachImage.setBackground(pill(Color.TRANSPARENT, Color.TRANSPARENT, dp(21)));
        attachImage.setContentDescription("Прикрепить изображение");
        LinearLayout.LayoutParams attachLp = new LinearLayout.LayoutParams(dp(42), dp(42));
        attachLp.leftMargin = dp(6);
        composer.addView(attachImage, attachLp);
        attachImage.setOnClickListener(v -> pickImage());

        send = new ImageButton(this);
        send.setImageResource(R.drawable.ic_send);
        send.setColorFilter(TEXT);
        send.setScaleType(ImageView.ScaleType.CENTER);
        send.setPadding(dp(9), dp(9), dp(9), dp(9));
        send.setBackground(gradientPill(WARP, WARP_DEEP, WARP, dp(22)));
        send.setContentDescription("Отправить сообщение");
        LinearLayout.LayoutParams sendLp = new LinearLayout.LayoutParams(dp(42), dp(42));
        sendLp.leftMargin = dp(6);
        composer.addView(send, sendLp);
        send.setOnClickListener(v -> submit());

        // Modern composer order: attachment, one borderless text surface,
        // then a contextual voice/send action. No nested control frames.
        composer.removeAllViews();
        LinearLayout.LayoutParams modernAttachLp = new LinearLayout.LayoutParams(dp(48), dp(48));
        composer.addView(attachImage, modernAttachLp);
        LinearLayout.LayoutParams modernInputLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        composer.addView(input, modernInputLp);
        LinearLayout.LayoutParams modernVoiceLp = new LinearLayout.LayoutParams(dp(48), dp(48));
        modernVoiceLp.leftMargin = dp(2);
        composer.addView(chatVoiceButton, modernVoiceLp);
        LinearLayout.LayoutParams modernSendLp = new LinearLayout.LayoutParams(dp(48), dp(48));
        modernSendLp.leftMargin = dp(2);
        composer.addView(send, modernSendLp);
        updateComposerActions();

        translatorView = buildTranslatorView();
        translatorView.setVisibility(View.GONE);
        contentHost.addView(translatorView, new FrameLayout.LayoutParams(-1, -1));

        agentView = buildAgentView();
        agentView.setVisibility(View.GONE);
        contentHost.addView(agentView, new FrameLayout.LayoutParams(-1, -1));

        memoryView = buildMemoryView();
        memoryView.setVisibility(View.GONE);
        contentHost.addView(memoryView, new FrameLayout.LayoutParams(-1, -1));

        progress = new ProgressBar(this);
        progress.setIndeterminate(true);
        progress.setVisibility(View.GONE);
        LinearLayout.LayoutParams progressLp = new LinearLayout.LayoutParams(dp(32), dp(32));
        progressLp.leftMargin = dp(6);
        titleRow.addView(progress, progressLp);

        buildDrawer(root);
        setContentView(root);
        installSystemInsets(root, mainColumn);
    }

    private LinearLayout buildCommandPalette() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.HORIZONTAL);
        panel.setGravity(Gravity.CENTER_VERTICAL);
        panel.setPadding(dp(2), dp(2), dp(2), dp(8));
        addCommandChip(panel, "⚔ Задача", "/task ");
        addCommandChip(panel, "⌁ Память", null);
        addCommandChip(panel, "◈ Анализ", "Проанализируй приложенное изображение: ");
        return panel;
    }

    private void addCommandChip(LinearLayout panel, String label, String command) {
        TextView chip = new TextView(this);
        chip.setText(label);
        chip.setTextColor(TEXT);
        chip.setTextSize(12);
        chip.setTypeface(Typeface.DEFAULT_BOLD);
        chip.setGravity(Gravity.CENTER);
        chip.setBackground(pill(SURFACE_RAISED, LINE, dp(15)));
        chip.setContentDescription(label);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(48), 1);
        if (panel.getChildCount() > 0) lp.leftMargin = dp(7);
        panel.addView(chip, lp);
        chip.setOnClickListener(v -> {
            v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
            if (command == null) {
                input.setText("");
                showTab(TAB_MEMORY);
                return;
            }
            input.setText(command);
            input.setSelection(command.length());
            commandPalette.setVisibility(View.GONE);
            input.requestFocus();
        });
    }

    private void updateCommandPalette(String value) {
        if (commandPalette == null) return;
        String clean = value == null ? "" : value.trim();
        commandPalette.setVisibility("/".equals(clean) ? View.VISIBLE : View.GONE);
        if (appRoot != null) appRoot.requestApplyInsets();
    }

    private void updateComposerActions() {
        if (send == null || chatVoiceButton == null || input == null) return;
        boolean hasText = !input.getText().toString().trim().isEmpty();
        boolean hasAttachment = pendingImageDataUrl != null && !pendingImageDataUrl.isEmpty();
        boolean canSend = hasText || hasAttachment;
        send.setVisibility(canSend ? View.VISIBLE : View.GONE);
        chatVoiceButton.setVisibility(canSend ? View.GONE : View.VISIBLE);
    }

    private LinearLayout buildBottomNavigation() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER);
        bar.setPadding(0, dp(3), 0, dp(3));
        bar.setBackgroundColor(Color.TRANSPARENT);
        addNavItem(bar, TAB_CHAT, "ЧАТ");
        addNavItem(bar, TAB_AGENT, "WARBANDS");
        addNavItem(bar, TAB_TRANSLATOR, "ПЕРЕВОД");
        addNavItem(bar, TAB_MEMORY, "ПАМЯТЬ");
        updateBottomNavigation();
        return bar;
    }

    private void addNavItem(LinearLayout bar, String tab, String label) {
        TextView item = new TextView(this);
        item.setText(label);
        item.setTextSize(10);
        item.setTypeface(Typeface.DEFAULT_BOLD);
        item.setGravity(Gravity.CENTER);
        item.setLetterSpacing(0.06f);
        item.setContentDescription("Открыть раздел " + label);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(48), 1);
        if (bar.getChildCount() > 0) lp.leftMargin = dp(4);
        bar.addView(item, lp);
        navItems.put(tab, item);
        item.setOnClickListener(v -> {
            v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
            showTab(tab);
        });
    }

    private void updateBottomNavigation() {
        for (java.util.Map.Entry<String, TextView> entry : navItems.entrySet()) {
            boolean selected = entry.getKey().equals(currentTab);
            TextView item = entry.getValue();
            item.setTextColor(selected ? ACID : TEXT_MUTED);
            item.setBackground(selected
                    ? pill(SURFACE_RAISED, ACID, dp(14))
                    : pill(Color.TRANSPARENT, Color.TRANSPARENT, dp(14)));
        }
    }

    private void startWarpPulse() {
        if (warpOrb == null) return;
        warpAnimator = ValueAnimator.ofFloat(0f, 1f);
        warpAnimator.setDuration(1700);
        warpAnimator.setRepeatCount(ValueAnimator.INFINITE);
        warpAnimator.setRepeatMode(ValueAnimator.REVERSE);
        warpAnimator.addUpdateListener(animation -> {
            float value = (float) animation.getAnimatedValue();
            warpOrb.setAlpha(0.72f + value * 0.28f);
            warpOrb.setScaleX(0.94f + value * 0.08f);
            warpOrb.setScaleY(0.94f + value * 0.08f);
        });
        warpAnimator.start();
    }

    private void setWarpState(String label, int color) {
        if (endpoint != null) {
            endpoint.setText(label);
            endpoint.setTextColor(color);
        }
        if (warpOrb != null) warpOrb.setBackground(gradientPill(color, WARP_DEEP, color, dp(20)));
    }

    private void restoreHeaderState() {
        if (TAB_AGENT.equals(currentTab)) {
            setWarpState(agentRunning ? "●  АБАДДОН / В РАБОТЕ" : "●  АБАДДОН / МОНИТОР", agentRunning ? WARP : CYAN);
        } else if (TAB_TRANSLATOR.equals(currentTab)) {
            setWarpState("●  ПЕРЕВОДЧИК / ГОТОВ", CYAN);
        } else if (TAB_MEMORY.equals(currentTab)) {
            setWarpState("●  АРХИВ / SHUSHUNYA", CYAN);
        } else {
            setWarpState(waiting ? "●  ШУШУНЯ ПЛЕТЁТ ОТВЕТ" : "●  ВАРП-КАНАЛ / В СЕТИ", waiting ? WARP : CYAN);
        }
    }

    private void installSystemInsets(View root, LinearLayout mainColumn) {
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            android.graphics.Insets ime = insets.getInsets(WindowInsets.Type.ime());
            int imeBottom = insets.isVisible(WindowInsets.Type.ime()) ? ime.bottom : 0;
            int bottom = Math.max(bars.bottom, imeBottom);
            lastKeyboardHeight = imeBottom;
            mainColumn.setPadding(dp(16), bars.top + dp(8), dp(16), bottom + dp(10));
            if (imeBottom > 0 && TAB_CHAT.equals(currentTab)) {
                root.postDelayed(() -> maybeScrollToBottom(false), 80);
            }
            return insets;
        });
        root.requestApplyInsets();
    }

    private void updateChatKeyboardLift() {
        if (appRoot != null) appRoot.requestApplyInsets();
        if (TAB_CHAT.equals(currentTab)) maybeScrollToBottom(false);
    }

    private LinearLayout buildAgentView() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        view.setPadding(0, dp(6), 0, 0);

        agentStatus = new TextView(this);
        agentStatus.setText("АБАДДОН • WARBANDS");
        agentStatus.setTextColor(CYAN);
        agentStatus.setTextSize(12);
        agentStatus.setTypeface(Typeface.DEFAULT_BOLD);
        agentStatus.setLetterSpacing(0.05f);
        agentStatus.setSingleLine(true);
        agentStatus.setEllipsize(TextUtils.TruncateAt.END);
        agentStatus.setPadding(dp(4), dp(2), dp(4), dp(8));
        view.addView(agentStatus, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout metrics = new LinearLayout(this);
        metrics.setOrientation(LinearLayout.HORIZONTAL);
        metrics.setPadding(0, 0, 0, dp(5));
        agentActiveMetric = addAgentMetric(metrics, "0", "АКТИВНО", WARP);
        agentDoneMetric = addAgentMetric(metrics, "0", "ГОТОВО", ACID);
        agentModeMetric = addAgentMetric(metrics, "LIVE", "КАНАЛ", CYAN);

        agentRunButton = new ImageButton(this);
        agentRunButton.setImageResource(R.drawable.ic_close);
        agentRunButton.setColorFilter(TEXT);
        agentRunButton.setScaleType(ImageView.ScaleType.CENTER);
        agentRunButton.setPadding(dp(14), dp(14), dp(14), dp(14));
        agentRunButton.setBackground(pill(Color.rgb(87, 23, 33), Color.rgb(231, 95, 69), dp(18)));
        agentRunButton.setContentDescription("Отменить текущую задачу Warbands");
        agentRunButton.setVisibility(View.GONE);
        LinearLayout.LayoutParams cancelLp = new LinearLayout.LayoutParams(dp(56), dp(60));
        cancelLp.leftMargin = dp(7);
        metrics.addView(agentRunButton, cancelLp);
        agentRunButton.setOnClickListener(v -> cancelAgentTask());
        view.addView(metrics, new LinearLayout.LayoutParams(-1, dp(66)));

        agentScrollView = new ScrollView(this);
        agentScrollView.setFillViewport(false);
        agentScrollView.setClipToPadding(false);
        agentScrollView.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        agentScrollView.setOnTouchListener((v, event) -> {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                agentTouchActive = true;
                agentPinnedScroll = true;
                if (agentScrollAnimator != null) {
                    agentScrollAnimator.cancel();
                    agentScrollAnimator = null;
                }
            }
            if (event.getAction() == MotionEvent.ACTION_UP || event.getAction() == MotionEvent.ACTION_CANCEL) {
                agentTouchActive = false;
                agentPinnedScroll = !isAtAgentBottom();
            }
            return false;
        });
        agentMessageList = new LinearLayout(this);
        agentMessageList.setOrientation(LinearLayout.VERTICAL);
        agentMessageList.setPadding(0, dp(12), 0, dp(12));
        agentScrollView.addView(agentMessageList, new ScrollView.LayoutParams(-1, -2));
        view.addView(agentScrollView, new LinearLayout.LayoutParams(-1, 0, 1));

        addAgentMessage(false, "Warbands готовы. Задачи отправляй из основного чата через /task, /w, /abaddon или «абаддон:».", false);

        LinearLayout quickRow = new LinearLayout(this);
        quickRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams quickLp = new LinearLayout.LayoutParams(-1, dp(50));
        quickLp.bottomMargin = dp(6);
        view.addView(quickRow, quickLp);

        Button statusButton = new Button(this);
        statusButton.setText("ИСКАНДАР");
        styleAgentQuickButton(statusButton);
        quickRow.addView(statusButton, new LinearLayout.LayoutParams(0, dp(48), 1));
        agentFilterButtons.put("IskandarKhayon", statusButton);
        statusButton.setOnClickListener(v -> setAgentBrigadeFilter("IskandarKhayon"));

        Button workButton = new Button(this);
        workButton.setText("ЦЕРАКСИЯ");
        styleAgentQuickButton(workButton);
        LinearLayout.LayoutParams workLp = new LinearLayout.LayoutParams(0, dp(48), 1);
        workLp.leftMargin = dp(8);
        quickRow.addView(workButton, workLp);
        agentFilterButtons.put("Ceraxia", workButton);
        workButton.setOnClickListener(v -> setAgentBrigadeFilter("Ceraxia"));

        Button focusButton = new Button(this);
        focusButton.setText("МОРИАНА");
        styleAgentQuickButton(focusButton);
        LinearLayout.LayoutParams focusLp = new LinearLayout.LayoutParams(0, dp(48), 1);
        focusLp.leftMargin = dp(8);
        quickRow.addView(focusButton, focusLp);
        agentFilterButtons.put("Moriana", focusButton);
        focusButton.setOnClickListener(v -> setAgentBrigadeFilter("Moriana"));

        Button stateButton = new Button(this);
        stateButton.setText("ВСЕ");
        styleAgentQuickButton(stateButton);
        LinearLayout.LayoutParams stateLp = new LinearLayout.LayoutParams(0, dp(48), 1);
        stateLp.leftMargin = dp(8);
        quickRow.addView(stateButton, stateLp);
        agentFilterButtons.put("", stateButton);
        stateButton.setOnClickListener(v -> setAgentBrigadeFilter(""));

        updateAgentFilterStyles();

        return view;
    }

    private TextView addAgentMetric(LinearLayout row, String value, String label, int accent) {
        LinearLayout cell = new LinearLayout(this);
        cell.setOrientation(LinearLayout.VERTICAL);
        cell.setGravity(Gravity.CENTER);
        cell.setBackground(pill(SURFACE_RAISED, Color.argb(150, Color.red(accent), Color.green(accent), Color.blue(accent)), dp(16)));
        TextView number = new TextView(this);
        number.setText(value);
        number.setTextColor(accent);
        number.setTextSize(18);
        number.setTypeface(Typeface.DEFAULT_BOLD);
        number.setGravity(Gravity.CENTER);
        TextView caption = new TextView(this);
        caption.setText(label);
        caption.setTextColor(TEXT_MUTED);
        caption.setTextSize(9);
        caption.setTypeface(Typeface.DEFAULT_BOLD);
        caption.setLetterSpacing(0.08f);
        caption.setGravity(Gravity.CENTER);
        cell.addView(number, new LinearLayout.LayoutParams(-1, dp(30)));
        cell.addView(caption, new LinearLayout.LayoutParams(-1, dp(20)));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(60), 1);
        if (row.getChildCount() > 0) lp.leftMargin = dp(7);
        row.addView(cell, lp);
        return number;
    }

    private void styleAgentQuickButton(Button button) {
        button.setTextColor(TEXT_MUTED);
        button.setTextSize(11);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setPadding(dp(4), 0, dp(4), 0);
        button.setBackground(pill(SURFACE_RAISED, LINE, dp(15)));
    }

    private void updateAgentFilterStyles() {
        for (java.util.Map.Entry<String, Button> entry : agentFilterButtons.entrySet()) {
            boolean selected = entry.getKey().equals(agentBrigadeFilter);
            Button button = entry.getValue();
            button.setTextColor(selected ? ACID : TEXT_MUTED);
            button.setBackground(selected
                    ? pill(SURFACE_RAISED, ACID, dp(15))
                    : pill(SURFACE_RAISED, LINE, dp(15)));
        }
    }

    private LinearLayout buildTranslatorView() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        view.setPadding(0, dp(10), 0, 0);

        speechStatus = new TextView(this);
        speechStatus.setText("Выбери направление. Микрофон пишет в исходный текст.");
        speechStatus.setTextColor(CYAN);
        speechStatus.setTextSize(12);
        speechStatus.setTypeface(Typeface.DEFAULT_BOLD);
        speechStatus.setLetterSpacing(0.04f);
        speechStatus.setPadding(dp(4), 0, dp(4), dp(8));
        view.addView(speechStatus, new LinearLayout.LayoutParams(-1, -2));

        translatorSourceText = translatorEdit("Исходный текст");
        LinearLayout.LayoutParams sourceLp = new LinearLayout.LayoutParams(-1, 0, 1);
        sourceLp.topMargin = dp(4);
        view.addView(translatorFieldBox(translatorSourceText), sourceLp);

        translatorResultText = translatorEdit("Перевод");
        LinearLayout.LayoutParams resultLp = new LinearLayout.LayoutParams(-1, 0, 1);
        resultLp.topMargin = dp(10);
        view.addView(translatorFieldBox(translatorResultText), resultLp);

        LinearLayout directionRow = new LinearLayout(this);
        directionRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams directionLp = new LinearLayout.LayoutParams(-1, dp(48));
        directionLp.topMargin = dp(10);
        view.addView(directionRow, directionLp);

        sourceLangLabel = languageLabel();
        targetLangLabel = languageLabel();
        swapDirectionButton = new Button(this);
        swapDirectionButton.setText("⇄");
        swapDirectionButton.setTextColor(INK);
        swapDirectionButton.setTextSize(22);
        swapDirectionButton.setTypeface(Typeface.DEFAULT_BOLD);
        swapDirectionButton.setBackground(pill(ACID, ACID, dp(22)));
        directionRow.addView(sourceLangLabel, new LinearLayout.LayoutParams(0, dp(44), 1));
        LinearLayout.LayoutParams swapLp = new LinearLayout.LayoutParams(dp(58), dp(44));
        swapLp.leftMargin = dp(10);
        swapLp.rightMargin = dp(10);
        directionRow.addView(swapDirectionButton, swapLp);
        directionRow.addView(targetLangLabel, new LinearLayout.LayoutParams(0, dp(44), 1));
        sourceLangLabel.setOnClickListener(v -> showLanguageDialog(true));
        targetLangLabel.setOnClickListener(v -> showLanguageDialog(false));
        swapDirectionButton.setOnClickListener(v -> setTranslatorLanguages(translatorTargetIndex, translatorSourceIndex));

        LinearLayout actionRow = new LinearLayout(this);
        actionRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams actionLp = new LinearLayout.LayoutParams(-1, dp(54));
        actionLp.topMargin = dp(8);
        view.addView(actionRow, actionLp);

        speechButton = new Button(this);
        speechButton.setText("REC");
        speechButton.setTextColor(TEXT);
        speechButton.setTextSize(14);
        speechButton.setTypeface(Typeface.DEFAULT_BOLD);
        speechButton.setBackground(pill(SURFACE_SOFT, WARP, dp(18)));
        actionRow.addView(speechButton, new LinearLayout.LayoutParams(dp(94), dp(52)));
        speechButton.setOnClickListener(v -> toggleSelectedLanguageRecording());

        translateButton = new Button(this);
        translateButton.setText("ПЕРЕВЕСТИ");
        translateButton.setTextColor(INK);
        translateButton.setTextSize(13);
        translateButton.setTypeface(Typeface.DEFAULT_BOLD);
        translateButton.setBackground(pill(ACID, ACID, dp(18)));
        LinearLayout.LayoutParams translateLp = new LinearLayout.LayoutParams(0, dp(52), 1);
        translateLp.leftMargin = dp(10);
        actionRow.addView(translateButton, translateLp);
        translateButton.setOnClickListener(v -> translateCurrentText());

        setTranslatorLanguages(0, 1);
        return view;
    }

    private TextView languageLabel() {
        TextView label = new TextView(this);
        label.setTextSize(16);
        label.setTypeface(Typeface.DEFAULT_BOLD);
        label.setGravity(Gravity.CENTER);
        label.setSingleLine(true);
        label.setTextColor(TEXT);
        label.setBackground(pill(SURFACE_RAISED, LINE, dp(18)));
        return label;
    }

    private FrameLayout translatorFieldBox(EditText edit) {
        FrameLayout box = new FrameLayout(this);
        box.addView(edit, new FrameLayout.LayoutParams(-1, -1));

        LinearLayout tools = new LinearLayout(this);
        tools.setGravity(Gravity.CENTER_VERTICAL);
        tools.setPadding(0, dp(6), dp(6), 0);
        Button clear = fieldToolButton("×");
        Button copy = fieldToolButton("⧉");
        tools.addView(clear, new LinearLayout.LayoutParams(dp(38), dp(34)));
        LinearLayout.LayoutParams copyLp = new LinearLayout.LayoutParams(dp(38), dp(34));
        copyLp.leftMargin = dp(6);
        tools.addView(copy, copyLp);
        FrameLayout.LayoutParams toolsLp = new FrameLayout.LayoutParams(-2, dp(44), Gravity.TOP | Gravity.RIGHT);
        box.addView(tools, toolsLp);

        clear.setOnClickListener(v -> edit.setText(""));
        copy.setOnClickListener(v -> copyEditText(edit));
        return box;
    }

    private Button fieldToolButton(String text) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextColor(TEXT_MUTED);
        button.setTextSize(20);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setPadding(0, 0, 0, dp(2));
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setBackground(pill(SURFACE_SOFT, LINE, dp(13)));
        return button;
    }

    private void copyEditText(EditText edit) {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (clipboard != null) {
            clipboard.setPrimaryClip(ClipData.newPlainText("shushunya-text", edit.getText().toString()));
            if (TAB_AGENT.equals(currentTab) && agentStatus != null) {
                agentStatus.setText("Скопировано.");
            } else if (speechStatus != null) {
                speechStatus.setText("Скопировано.");
            }
        }
    }

    private EditText translatorEdit(String hint) {
        EditText edit = new EditText(this);
        edit.setTextColor(TEXT);
        edit.setHint(hint);
        edit.setHintTextColor(TEXT_MUTED);
        edit.setTextSize(17);
        edit.setGravity(Gravity.TOP | Gravity.START);
        edit.setMinLines(3);
        edit.setSingleLine(false);
        edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        edit.setVerticalScrollBarEnabled(true);
        edit.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        edit.setScroller(new Scroller(this));
        edit.setPadding(dp(12), dp(42), dp(12), dp(10));
        edit.setBackground(pill(SURFACE, LINE, dp(18)));
        edit.setOnTouchListener((v, event) -> {
            if (edit.canScrollVertically(1) || edit.canScrollVertically(-1)) {
                v.getParent().requestDisallowInterceptTouchEvent(true);
                if (event.getAction() == MotionEvent.ACTION_UP || event.getAction() == MotionEvent.ACTION_CANCEL) {
                    v.getParent().requestDisallowInterceptTouchEvent(false);
                }
            }
            return false;
        });
        return edit;
    }

    private LinearLayout buildMemoryView() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        view.setPadding(0, dp(8), 0, 0);

        memoryStatus = new TextView(this);
        memoryStatus.setText("АРХИВ ШУШУНИ • СИНХРОНИЗАЦИЯ");
        memoryStatus.setTextColor(CYAN);
        memoryStatus.setTextSize(11);
        memoryStatus.setTypeface(Typeface.DEFAULT_BOLD);
        memoryStatus.setLetterSpacing(0.07f);
        memoryStatus.setPadding(dp(4), 0, dp(4), dp(9));
        view.addView(memoryStatus, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout searchRow = new LinearLayout(this);
        searchRow.setGravity(Gravity.CENTER_VERTICAL);
        memorySearch = new EditText(this);
        memorySearch.setSingleLine(true);
        memorySearch.setHint("Что Шушуня помнит?");
        memorySearch.setTextColor(TEXT);
        memorySearch.setHintTextColor(TEXT_MUTED);
        memorySearch.setTextSize(15);
        memorySearch.setPadding(dp(14), 0, dp(14), 0);
        memorySearch.setBackground(pill(SURFACE_RAISED, LINE, dp(18)));
        searchRow.addView(memorySearch, new LinearLayout.LayoutParams(0, dp(48), 1));

        TextView search = memoryActionButton("НАЙТИ");
        LinearLayout.LayoutParams searchLp = new LinearLayout.LayoutParams(dp(82), dp(48));
        searchLp.leftMargin = dp(8);
        searchRow.addView(search, searchLp);
        search.setOnClickListener(v -> searchMemory());
        memorySearch.setOnEditorActionListener((v, actionId, event) -> {
            searchMemory();
            return true;
        });
        view.addView(searchRow, new LinearLayout.LayoutParams(-1, dp(48)));

        ScrollView scroll = new ScrollView(this);
        scroll.setClipToPadding(false);
        memoryList = new LinearLayout(this);
        memoryList.setOrientation(LinearLayout.VERTICAL);
        memoryList.setPadding(0, dp(12), 0, dp(12));
        scroll.addView(memoryList, new ScrollView.LayoutParams(-1, -2));
        LinearLayout.LayoutParams scrollLp = new LinearLayout.LayoutParams(-1, 0, 1);
        scrollLp.topMargin = dp(6);
        view.addView(scroll, scrollLp);

        TextView propose = memoryActionButton("＋ ПРЕДЛОЖИТЬ ИЗМЕНЕНИЕ ПАМЯТИ");
        propose.setTextColor(INK);
        propose.setBackground(pill(ACID, ACID, dp(18)));
        LinearLayout.LayoutParams proposeLp = new LinearLayout.LayoutParams(-1, dp(48));
        proposeLp.topMargin = dp(5);
        view.addView(propose, proposeLp);
        propose.setOnClickListener(v -> showMemoryProposalDialog());
        return view;
    }

    private TextView memoryActionButton(String label) {
        TextView button = new TextView(this);
        button.setText(label);
        button.setTextColor(TEXT);
        button.setTextSize(11);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setGravity(Gravity.CENTER);
        button.setBackground(pill(SURFACE_SOFT, WARP, dp(18)));
        return button;
    }

    private void loadMemoryDashboard() {
        memoryStatus.setText("АРХИВ ШУШУНИ • ЧИТАЮ СЛОИ ПАМЯТИ");
        new Thread(() -> {
            try {
                JSONObject payload = memoryGet("/archive/memory/catalog?namespace=" + SERVER_MEMORY_NAMESPACE + "&requester=shushunya-mobile");
                main.post(() -> renderMemoryCatalog(payload));
            } catch (Exception exc) {
                main.post(() -> renderMemoryOffline(exc.getMessage()));
            }
        }).start();
    }

    private void renderMemoryOffline(String reason) {
        memoryList.removeAllViews();
        memoryStatus.setText("АРХИВ ВНЕ КАНАЛА");
        addMemoryHero("ПАМЯТЬ СПИТ", "Связь с архивом временно потеряна. Твои данные не пропали.");
        TextView retry = memoryActionButton("↻  ПОВТОРИТЬ ПОДКЛЮЧЕНИЕ");
        retry.setContentDescription("Повторить подключение к памяти");
        LinearLayout.LayoutParams retryLp = new LinearLayout.LayoutParams(-1, dp(48));
        retryLp.topMargin = dp(10);
        memoryList.addView(retry, retryLp);
        retry.setOnClickListener(v -> loadMemoryDashboard());
        if (reason != null && !reason.trim().isEmpty()) {
            TextView detail = memoryCardText(reason, 11, TEXT_MUTED, false);
            detail.setPadding(dp(4), dp(10), dp(4), 0);
            memoryList.addView(detail, new LinearLayout.LayoutParams(-1, -2));
        }
    }

    private void renderMemoryCatalog(JSONObject payload) {
        memoryList.removeAllViews();
        JSONObject focus = payload.optJSONObject("focus");
        JSONObject wiki = payload.optJSONObject("wiki");
        JSONArray books = focus == null ? null : focus.optJSONArray("books");
        JSONArray pages = wiki == null ? null : wiki.optJSONArray("pages");
        int bookCount = books == null ? 0 : books.length();
        int pageCount = pages == null ? 0 : pages.length();
        addMemoryHero("ПАМЯТЬ В СЕТИ", bookCount + " активных фокусов  •  " + pageCount + " страниц знания");
        addMemoryArray("АКТИВНЫЙ КОНТЕКСТ", books, 4);
        addMemoryArray("ЛИЧНАЯ ВИКИ", pages, 8);
        if (bookCount == 0 && pageCount == 0) addMemoryCard("Пока пусто", "Архив ответил, но карточек памяти ещё нет.", CYAN);
        memoryStatus.setText("АРХИВ ШУШУНИ • ОБНОВЛЕНО");
    }

    private void addMemoryArray(String section, JSONArray values, int limit) {
        if (values == null || values.length() == 0) return;
        TextView heading = new TextView(this);
        heading.setText(section);
        heading.setTextColor(TEXT_MUTED);
        heading.setTextSize(10);
        heading.setTypeface(Typeface.DEFAULT_BOLD);
        heading.setLetterSpacing(0.10f);
        heading.setPadding(dp(4), dp(12), dp(4), dp(5));
        memoryList.addView(heading, new LinearLayout.LayoutParams(-1, -2));
        for (int i = 0; i < Math.min(limit, values.length()); i++) {
            Object raw = values.opt(i);
            JSONObject item = raw instanceof JSONObject ? (JSONObject) raw : null;
            String titleText = item == null ? String.valueOf(raw) : item.optString("title", item.optString("id", "Запись"));
            String detail = item == null ? "" : item.optString("summary", item.optString("updated_at", item.optString("id", "")));
            addMemoryCard(titleText, detail, WARP);
        }
    }

    private void addMemoryHero(String titleText, String detail) {
        LinearLayout card = memoryCardBase(ACID);
        TextView titleView = memoryCardText(titleText, 19, ACID, true);
        TextView body = memoryCardText(detail, 13, TEXT, false);
        card.addView(titleView);
        card.addView(body);
        memoryList.addView(card, memoryCardLayout());
    }

    private void addMemoryCard(String titleText, String detail, int accent) {
        LinearLayout card = memoryCardBase(accent);
        card.addView(memoryCardText(titleText, 16, TEXT, true));
        if (detail != null && !detail.trim().isEmpty()) card.addView(memoryCardText(detail, 12, TEXT_MUTED, false));
        memoryList.addView(card, memoryCardLayout());
    }

    private LinearLayout memoryCardBase(int accent) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(14), dp(16), dp(14));
        card.setBackground(pill(SURFACE_RAISED, Color.argb(170, Color.red(accent), Color.green(accent), Color.blue(accent)), dp(17)));
        return card;
    }

    private LinearLayout.LayoutParams memoryCardLayout() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(7);
        return lp;
    }

    private TextView memoryCardText(String value, int size, int color, boolean bold) {
        TextView text = new TextView(this);
        text.setText(value == null ? "" : value);
        text.setTextColor(color);
        text.setTextSize(size);
        text.setLineSpacing(dp(2), 1f);
        if (bold) text.setTypeface(Typeface.DEFAULT_BOLD);
        return text;
    }

    private void searchMemory() {
        String query = memorySearch.getText().toString().trim();
        if (query.isEmpty()) {
            loadMemoryDashboard();
            return;
        }
        memoryStatus.setText("ИЩУ В ПАМЯТИ • " + query.toUpperCase());
        new Thread(() -> {
            try {
                JSONObject payload = memoryGet("/archive/memory/search?namespace=" + SERVER_MEMORY_NAMESPACE
                        + "&requester=shushunya-mobile&include_content=true&limit=8&q=" + Uri.encode(query));
                main.post(() -> renderMemorySearch(payload, query));
            } catch (Exception exc) {
                main.post(() -> memoryStatus.setText("ПОИСК НЕ УДАЛСЯ • " + exc.getMessage()));
            }
        }).start();
    }

    private void renderMemorySearch(JSONObject payload, String query) {
        memoryList.removeAllViews();
        addMemoryHero("РЕЗУЛЬТАТЫ", "Поиск по всем слоям: «" + query + "»");
        int count = 0;
        for (String layer : new String[]{"focus", "wiki", "vector"}) {
            JSONArray values = payload.optJSONArray(layer);
            if (values == null) continue;
            for (int i = 0; i < Math.min(6, values.length()); i++) {
                JSONObject item = values.optJSONObject(i);
                if (item == null) continue;
                String titleText = item.optString("title", item.optString("id", layer.toUpperCase()));
                String detail = item.optString("content", item.optString("text", item.optString("summary", "")));
                if (detail.length() > 360) detail = detail.substring(0, 360) + "…";
                addMemoryCard(titleText, detail, "vector".equals(layer) ? CYAN : WARP);
                count++;
            }
        }
        if (count == 0) addMemoryCard("Ничего не найдено", "Попробуй другую формулировку.", CYAN);
        memoryStatus.setText("ПАМЯТЬ • НАЙДЕНО: " + count);
    }

    private JSONObject memoryGet(String path) throws Exception {
        URL url = new URL(trimSlash(baseUrl) + path);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);
        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) throw new IllegalStateException("HTTP " + code);
        return new JSONObject(response);
    }

    private void showMemoryProposalDialog() {
        EditText proposal = new EditText(this);
        proposal.setHint("Например: забудь старый адрес или запомни новое предпочтение");
        proposal.setTextColor(TEXT);
        proposal.setHintTextColor(TEXT_MUTED);
        proposal.setMinLines(4);
        proposal.setPadding(dp(16), dp(14), dp(16), dp(14));
        proposal.setBackground(pill(SURFACE_RAISED, LINE, dp(16)));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Изменение памяти")
                .setView(proposal)
                .setPositiveButton("Отправить архивариусу", null)
                .setNegativeButton("Отмена", null)
                .create();
        dialog.setOnShowListener(d -> {
            Window window = dialog.getWindow();
            if (window != null) window.setBackgroundDrawable(pill(SURFACE, WARP, dp(18)));
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setTextColor(ACID);
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
                String value = proposal.getText().toString().trim();
                if (value.isEmpty()) return;
                dialog.dismiss();
                proposeMemoryChange(value);
            });
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setTextColor(TEXT_MUTED);
        });
        dialog.show();
    }

    private void proposeMemoryChange(String proposal) {
        memoryStatus.setText("АРХИВАРИУС ПРОВЕРЯЕТ ИЗМЕНЕНИЕ");
        new Thread(() -> {
            try {
                URL url = new URL(trimSlash(baseUrl) + "/archive/memory/propose-change");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(12000);
                conn.setReadTimeout(60000);
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                applyMobileAuth(conn);
                JSONObject body = new JSONObject();
                body.put("namespace", SERVER_MEMORY_NAMESPACE);
                body.put("requester", "shushunya-mobile");
                body.put("proposal", proposal);
                body.put("target", "auto");
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                int code = conn.getResponseCode();
                if (code < 200 || code >= 300) throw new IllegalStateException("HTTP " + code);
                main.post(() -> {
                    memoryStatus.setText("ИЗМЕНЕНИЕ ПРИНЯТО НА ПРОВЕРКУ");
                    Toast.makeText(this, "Архивариус получил предложение", Toast.LENGTH_SHORT).show();
                });
            } catch (Exception exc) {
                main.post(() -> memoryStatus.setText("НЕ УДАЛОСЬ ИЗМЕНИТЬ • " + exc.getMessage()));
            }
        }).start();
    }

    private void showLanguageDialog(boolean sourceSide) {
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle(sourceSide ? "С какого языка" : "На какой язык")
                .setItems(TRANSLATOR_NAMES, (d, which) -> {
                    if (sourceSide) {
                        setTranslatorLanguages(which, translatorTargetIndex == which ? translatorSourceIndex : translatorTargetIndex);
                    } else {
                        setTranslatorLanguages(translatorSourceIndex == which ? translatorTargetIndex : translatorSourceIndex, which);
                    }
                })
                .create();
        dialog.setOnShowListener(d -> {
            Window window = dialog.getWindow();
            if (window != null) {
                window.setBackgroundDrawable(pill(SURFACE, WARP, dp(18)));
            }
        });
        dialog.show();
    }

    private void setTranslatorLanguages(int sourceIndex, int targetIndex) {
        translatorSourceIndex = sourceIndex;
        translatorTargetIndex = targetIndex;
        if (sourceLangLabel != null && targetLangLabel != null) {
            sourceLangLabel.setText(TRANSLATOR_NAMES[sourceIndex]);
            targetLangLabel.setText(TRANSLATOR_NAMES[targetIndex]);
            sourceLangLabel.setTextColor(TEXT);
            targetLangLabel.setTextColor(TEXT);
        }
        if (speechButton != null) {
            speechButton.setText("REC " + TRANSLATOR_SHORT[sourceIndex]);
        }
    }

    private void toggleSelectedLanguageRecording() {
        toggleWhisperRecording(
                TRANSLATOR_STT_CODES[translatorSourceIndex],
                translatorSourceText,
                TRANSLATOR_NAMES[translatorSourceIndex],
                speechButton);
    }

    private void translateCurrentText() {
        String text = translatorSourceText.getText().toString().trim();
        if (text.isEmpty() || translating) {
            return;
        }
        if (translatorSourceIndex == translatorTargetIndex) {
            speechStatus.setText("Выбери разные языки.");
            return;
        }
        translating = true;
        translateButton.setEnabled(false);
        translateButton.animate().alpha(0.55f).setDuration(160).start();
        speechStatus.setText("Перевожу: " + TRANSLATOR_SHORT[translatorSourceIndex] + " → " + TRANSLATOR_SHORT[translatorTargetIndex]);

        new Thread(() -> {
            try {
                String jobId = requestTranslationStart(
                        TRANSLATOR_CODES[translatorSourceIndex],
                        TRANSLATOR_CODES[translatorTargetIndex],
                        text);
                String result = pollTranslationJobUntilDone(jobId);
                main.post(() -> {
                    translating = false;
                    translateButton.setEnabled(true);
                    translateButton.animate().alpha(1f).setDuration(160).start();
                    translatorResultText.setText(result);
                    translatorResultText.setSelection(translatorResultText.getText().length());
                    speechStatus.setText("Готово.");
                });
            } catch (Exception exc) {
                main.post(() -> {
                    translating = false;
                    translateButton.setEnabled(true);
                    translateButton.animate().alpha(1f).setDuration(160).start();
                    speechStatus.setText("Ошибка перевода "
                            + TRANSLATOR_SHORT[translatorSourceIndex]
                            + " → "
                            + TRANSLATOR_SHORT[translatorTargetIndex]
                            + ": "
                            + exc.getMessage());
                });
            }
        }).start();
    }

    private String requestTranslationStart(String source, String target, String text) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("source", source);
        payload.put("target", target);
        payload.put("text", text);

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/translate/start");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        JSONObject json = new JSONObject(response);
        if (!json.optBoolean("ok", false)) {
            throw new IllegalStateException(json.optString("error", response));
        }
        return json.optString("job_id", "");
    }

    private String pollTranslationJobUntilDone(String jobId) throws Exception {
        if (jobId == null || jobId.trim().isEmpty()) {
            throw new IllegalStateException("empty translation job id");
        }
        while (true) {
            JSONObject snapshot = requestMobileJobSnapshot(jobId);
            String status = snapshot.optString("status", "");
            if ("done".equals(status)) {
                JSONObject response = snapshot.optJSONObject("response");
                if (response == null) {
                    return "";
                }
                return response.optString("translation", "").trim();
            }
            if ("failed".equals(status)) {
                throw new IllegalStateException(snapshot.optString("error", "translation job failed"));
            }
            Thread.sleep(900);
        }
    }

    private String requestTranslation(String source, String target, String text) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("source", source);
        payload.put("target", target);
        payload.put("text", text);

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/translate");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(180000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        return new JSONObject(response).optString("translation", "").trim();
    }

    private String warmasterSubmitFailedMessage() {
        return "Не удалось передать задачу на сервер. Ничего не запущено.";
    }

    private String warmasterMonitorDetachedMessage(String taskId) {
        String cleanTaskId = taskId == null ? "" : taskId.trim();
        return cleanTaskId.isEmpty()
                ? "Экран не смог обновить ход работы. Если задача уже была принята сервером, она продолжает выполняться на ПК."
                : "Экран не смог обновить ход работы. Задача " + cleanTaskId + " не остановлена и продолжает жить на ПК.";
    }

    private void loadAgentHistoryAndRestore() {
        new Thread(() -> {
            String storedTaskId = getSharedPreferences(PREFS, MODE_PRIVATE).getString("current_agent_task_id", "");
            try {
                JSONObject payload = requestAgentTaskList();
                JSONArray tasks = payload.optJSONArray("tasks");
                String runningTaskId = "";
                boolean storedTaskFinished = false;
                String cleanStoredTaskId = storedTaskId == null ? "" : storedTaskId.trim();
                if (tasks != null) {
                    for (int i = 0; i < tasks.length(); i++) {
                        JSONObject task = tasks.optJSONObject(i);
                        if (task == null) {
                            continue;
                        }
                        String taskId = task.optString("task_id", "").trim();
                        boolean running = task.optBoolean("running", false);
                        if (taskId.equals(cleanStoredTaskId) && !running) {
                            storedTaskFinished = true;
                        }
                        if (running) {
                            runningTaskId = task.optString("task_id", "").trim();
                            break;
                        }
                    }
                }
                String restoreTaskId = !runningTaskId.isEmpty() ? runningTaskId : (storedTaskFinished ? "" : cleanStoredTaskId);
                boolean shouldClearStoredTaskId = storedTaskFinished && restoreTaskId.isEmpty();
                main.post(() -> renderAgentTaskHistory(tasks));
                if (!restoreTaskId.isEmpty()) {
                    main.post(() -> restoreAgentTask(restoreTaskId));
                } else if (shouldClearStoredTaskId) {
                    main.post(() -> getSharedPreferences(PREFS, MODE_PRIVATE)
                            .edit()
                            .remove("current_agent_task_id")
                            .apply());
                }
            } catch (Exception exc) {
                String fallbackTaskId = storedTaskId == null ? "" : storedTaskId.trim();
                if (!fallbackTaskId.isEmpty()) {
                    main.post(() -> restoreAgentTask(fallbackTaskId));
                } else {
                    main.post(() -> agentStatus.setText("Историю Warbands сейчас не удалось обновить. Задачи на ПК от этого не останавливаются."));
                }
            }
        }).start();
    }

    private void restoreAgentTask(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        currentAgentTaskId = taskId.trim();
        agentDisplayedEventCount = 0;
        agentRunning = true;
        agentCancelRequested = false;
        setAgentRunButtonRunning(true);
        if (agentStatus != null) {
            agentStatus.setText("Восстанавливаю состояние Абаддона...");
        }
        if (agentLiveBubble == null) {
            agentLiveBubble = addAgentMessage(false, "", false);
        }
        progress.setVisibility(View.VISIBLE);
        new Thread(() -> {
            try {
                String result = pollAgentTaskUntilDone(currentAgentTaskId);
                main.post(() -> {
                    agentRunning = false;
                    agentCancelRequested = false;
                    currentAgentTaskId = "";
                    getSharedPreferences(PREFS, MODE_PRIVATE)
                            .edit()
                            .remove("current_agent_task_id")
                            .apply();
                    setAgentRunButtonRunning(false);
                    progress.setVisibility(waiting ? View.VISIBLE : View.GONE);
                    agentStatus.setText(result.toLowerCase().contains("остановлен") ? "Отменено." : "Готово.");
                    agentLiveBubble = null;
                });
            } catch (Exception exc) {
                String taskIdText = currentAgentTaskId == null ? "" : currentAgentTaskId.trim();
                String message = taskIdText.isEmpty()
                        ? "Мониторинг варбанды временно недоступен."
                        : warmasterMonitorDetachedMessage(taskIdText);
                main.post(() -> {
                    agentRunning = false;
                    agentCancelRequested = false;
                    setAgentRunButtonRunning(false);
                    progress.setVisibility(waiting ? View.VISIBLE : View.GONE);
                    agentStatus.setText(message);
                    appendAgentLog("! " + message);
                    agentLiveBubble = null;
                });
            } finally {
            }
        }).start();
    }

    private void setAgentRunButtonRunning(boolean running) {
        if (agentRunButton == null) {
            return;
        }
        agentRunButton.setVisibility(running ? View.VISIBLE : View.GONE);
        agentRunButton.setEnabled(true);
        agentRunButton.setImageResource(R.drawable.ic_close);
        agentRunButton.setColorFilter(TEXT);
        agentRunButton.setBackground(pill(Color.rgb(87, 23, 33), Color.rgb(231, 95, 69), dp(18)));
        agentRunButton.animate().alpha(agentCancelRequested ? 0.55f : 1f).setDuration(160).start();
    }

    private void cancelAgentTask() {
        if (!agentRunning || agentCancelRequested) {
            return;
        }
        String taskId = currentAgentTaskId == null ? "" : currentAgentTaskId.trim();
        if (taskId.isEmpty()) {
            agentStatus.setText("Нет task_id для отмены.");
            return;
        }
        agentCancelRequested = true;
        setAgentRunButtonRunning(true);
        agentStatus.setText("Отправляю отмену...");
        appendAgentLog("! Запрошена отмена задачи " + taskId);
        new Thread(() -> {
            try {
                String message = requestAgentCancel(taskId);
                main.post(() -> {
                    agentStatus.setText(message.isEmpty() ? "Отмена отправлена." : message);
                    appendAgentLog("! Отмена принята сервером.");
                });
            } catch (Exception exc) {
                main.post(() -> {
                    agentCancelRequested = false;
                    setAgentRunButtonRunning(true);
                    agentStatus.setText("Ошибка отмены: " + exc.getMessage());
                    appendAgentLog("! Ошибка отмены: " + exc.getMessage());
                });
            }
        }).start();
    }

    private void setAgentBrigadeFilter(String filter) {
        agentBrigadeFilter = filter == null ? "" : filter.trim();
        updateAgentFilterStyles();
        String label = agentBrigadeLabel(agentBrigadeFilter);
        if (agentStatus != null) {
            agentStatus.setText(label.isEmpty() ? "Показываю все варбанды..." : "Показываю: " + label);
        }
        agentSections.clear();
        lastAgentTasksJson = "";
        if (agentMessageList != null) {
            agentMessageList.removeAllViews();
        }
        refreshBrigadeMonitor();
    }

    private void refreshBrigadeMonitor() {
        new Thread(() -> {
            try {
                JSONObject payload = requestAgentTaskList();
                JSONArray tasks = payload.optJSONArray("tasks");
                main.post(() -> renderAgentTaskHistory(tasks));
            } catch (Exception exc) {
                // Quiet failure: the auto-poller retries in seconds; spamming
                // the feed with warning bubbles is worse than a stale view.
                main.post(() -> {
                    if (agentStatus != null) {
                        agentStatus.setText("Монитор варбанд сейчас не обновился.");
                    }
                });
            }
        }).start();
    }

    private void startBrigadeDeltaLoop() {
        if (brigadeDeltaLoopRunning) {
            return;
        }
        brigadeDeltaLoopRunning = true;
        new Thread(() -> {
            // Same delta model as the chat: one held request at a time, the
            // server answers when the meaningful brigade state changed.
            try {
                while (appInForeground && TAB_AGENT.equals(currentTab)) {
                    try {
                        JSONObject payload = requestAgentTaskList(brigadeStateKey, 25);
                        String newKey = payload.optString("state_key", "");
                        boolean changed = payload.optBoolean("changed", true);
                        if (!newKey.isEmpty()) {
                            brigadeStateKey = newKey;
                        }
                        if (changed) {
                            JSONArray tasks = payload.optJSONArray("tasks");
                            main.post(() -> renderAgentTaskHistory(tasks));
                        }
                    } catch (Exception transient_) {
                        Thread.sleep(3000);
                    }
                }
            } catch (InterruptedException ignored) {
            } finally {
                brigadeDeltaLoopRunning = false;
            }
        }).start();
    }

    private void refreshAgentState() {
        if (agentStatus != null) {
            agentStatus.setText("Проверяю состояние Абаддона...");
        }
        new Thread(() -> {
            try {
                String state = requestAgentState();
                main.post(() -> {
                    if (agentStatus != null) {
                        agentStatus.setText("Состояние Абаддона получено.");
                    }
                    addAgentMessage(false, state, true);
                });
            } catch (Exception exc) {
                main.post(() -> {
                    if (agentStatus != null) {
                        agentStatus.setText("Состояние Абаддона сейчас не обновилось.");
                    }
                    addAgentMessage(false, "! Состояние Абаддона сейчас не обновилось. Задачи на ПК от этого не останавливаются.", true);
                });
            }
        }).start();
    }

    private void appendAgentLog(String line) {
        if (agentLiveBubble == null || line == null || line.trim().isEmpty()) {
            return;
        }
        String prefix = agentLiveBubble.getText().length() == 0 ? "" : "\n";
        agentLiveBubble.append(prefix + line);
        maybeScrollAgentToBottom(false);
    }

    private void handleAgentEvent(JSONObject event) {
        String type = event.optString("type", "");
        if ("start".equals(type)) {
            agentStatus.setText(event.optString("message", "Абаддон стартует..."));
            appendAgentLog("• " + event.optString("message", "старт"));
            return;
        }
        if ("task".equals(type)) {
            String taskId = event.optString("task_id", "").trim();
            String namespace = event.optString("memory_namespace", SERVER_MEMORY_NAMESPACE).trim();
            if (!taskId.isEmpty()) {
                currentAgentTaskId = taskId;
            }
            agentStatus.setText(taskId.isEmpty() ? "Абаддон получил задачу." : "Задача " + taskId);
            appendAgentLog("• Память: " + namespace + (taskId.isEmpty() ? "" : ", task_id=" + taskId));
            return;
        }
        if ("step".equals(type)) {
            int step = event.optInt("step", 0);
            int maxSteps = event.optInt("max_steps", 0);
            appendAgentLog("• Шаг " + step + "/" + maxSteps + ": " + event.optString("message", "думаю"));
            agentStatus.setText("Шаг " + step + "/" + maxSteps);
            return;
        }
        if ("action".equals(type)) {
            String action = event.optString("action", "tool");
            String summary = event.optString("summary", "");
            String reason = event.optString("reason", "");
            String line = "→ " + action + (summary.isEmpty() ? "" : ": " + summary);
            if (!reason.isEmpty()) {
                line += " — " + reason;
            }
            appendAgentLog(line);
            return;
        }
        if ("tool_result".equals(type)) {
            String marker = event.optBoolean("ok", false) ? "✓" : "!";
            double duration = event.optDouble("duration_sec", -1.0);
            String suffix = duration >= 0.0 ? " (" + duration + "s)" : "";
            appendAgentLog(marker + " " + event.optString("action", "tool") + ": " + event.optString("message", "готово") + suffix);
            return;
        }
        if ("warning".equals(type)) {
            appendAgentLog("! " + event.optString("message", "предупреждение"));
            return;
        }
        if ("heartbeat".equals(type)) {
            double duration = event.optDouble("current_task_duration_sec", -1.0);
            if (duration >= 0.0) {
                agentStatus.setText("Абаддон думает... " + duration + "s");
            }
            return;
        }
        if ("final".equals(type)) {
            String message = event.optString("message", "").trim();
            double duration = event.optDouble("duration_sec", -1.0);
            boolean cancelled = event.optBoolean("cancelled", false);
            appendAgentLog("");
            appendAgentLog(cancelled
                    ? (duration >= 0.0 ? "Остановлено (" + duration + "s):" : "Остановлено:")
                    : (duration >= 0.0 ? "Результат (" + duration + "s):" : "Результат:"));
            appendAgentLog(message.isEmpty() ? "Абаддон вернул пустой ответ." : message);
            if (cancelled) {
                agentStatus.setText("Отменено.");
            }
            return;
        }
        if ("error".equals(type)) {
            appendAgentLog("! Ошибка: " + event.optString("message", "unknown"));
            return;
        }
        if ("done".equals(type)) {
            JSONObject result = event.optJSONObject("result");
            boolean cancelled = result != null && result.optBoolean("cancelled", false);
            agentStatus.setText(cancelled ? "Отменено." : event.optBoolean("ok", false) ? "Готово." : "Абаддон завершился с ошибкой.");
        }
    }

    private String requestAgentStart(String task, String taskId) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("message", task);
        payload.put("task_id", taskId);
        payload.put("session_id", SERVER_CHAT_SESSION_ID);
        payload.put("client_source", "app");

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/warmaster/start");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            if (code == 409) {
                throw new IllegalStateException("Абаддон занят, открой Warbands и повтори позже");
            }
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        JSONObject json = new JSONObject(response);
        if (!json.optBoolean("ok", false)) {
            throw new IllegalStateException(json.optString("error", response));
        }
        return json.optString("task_id", taskId);
    }

    private JSONObject requestAgentTaskSnapshot(String taskId) throws Exception {
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/warmaster/task?task_id=" + taskId + "&limit=160");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        return new JSONObject(response);
    }

    private JSONObject requestAgentTaskList() throws Exception {
        return requestAgentTaskList("", 0);
    }

    private JSONObject requestAgentTaskList(String stateKey, int waitSec) throws Exception {
        String suffix = (stateKey == null || stateKey.isEmpty() || waitSec <= 0)
                ? ""
                : "&state_key=" + stateKey + "&wait=" + waitSec;
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/warmaster/tasks?prefix=client&limit=" + AGENT_HISTORY_LIMIT + suffix);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(40000);
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        return new JSONObject(response);
    }

    private String agentTasksDiffKey(JSONArray tasks) {
        if (tasks == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < tasks.length(); i++) {
            JSONObject task = tasks.optJSONObject(i);
            if (task == null) {
                continue;
            }
            sb.append(task.optString("task_id", "")).append('|')
                    .append(task.optString("status", "")).append('|')
                    .append(task.optBoolean("running", false)).append('|')
                    .append(task.optString("current_step", "")).append('|')
                    .append(task.optString("final", "").hashCode()).append('|')
                    .append(task.optString("task", "").hashCode()).append('|');
            JSONObject missionState = task.optJSONObject("mission_state");
            sb.append(missionState == null ? "" : missionState.optString("user_visible_state", "")).append('|');
            JSONArray cards = task.optJSONArray("activity_cards");
            if (cards == null) {
                cards = task.optJSONArray("activity_entries");
            }
            if (cards != null) {
                for (int j = 0; j < cards.length(); j++) {
                    JSONObject card = cards.optJSONObject(j);
                    if (card == null) {
                        continue;
                    }
                    sb.append(card.optString("headline", "").hashCode()).append('~')
                            .append(card.optString("status", "")).append('~')
                            .append(card.optString("severity", "")).append('~')
                            .append(card.optString("detail", "").hashCode()).append(';');
                }
            }
            sb.append('\n');
        }
        return sb.toString();
    }

    private void renderAgentTaskHistory(JSONArray tasks) {
        if (agentMessageList == null) {
            return;
        }
        String key = agentTasksDiffKey(tasks);
        if (key.equals(lastAgentTasksJson)) {
            return;
        }
        lastAgentTasksJson = key;
        applyBrigadeTasks(tasks);
    }

    private void applyBrigadeTasks(JSONArray tasks) {
        // Incremental, messenger-style rendering: sections are created once,
        // task cards are swapped in place, activity cards are APPENDED and the
        // final message is added once. Nothing full-screen is ever rebuilt.
        boolean firstFill = agentSections.isEmpty();
        if (firstFill) {
            agentMessageList.removeAllViews();
        }
        int visible = 0;
        int length = tasks == null ? 0 : tasks.length();
        java.util.HashSet<String> visibleTaskIds = new java.util.HashSet<>();
        int activeCount = 0;
        int doneCount = 0;
        for (int i = 0; i < length; i++) {
            JSONObject metricTask = tasks.optJSONObject(i);
            if (metricTask == null || !agentTaskMatchesBrigade(metricTask)) continue;
            if (metricTask.optBoolean("running", false)) activeCount++; else doneCount++;
        }
        if (agentActiveMetric != null) agentActiveMetric.setText(String.valueOf(activeCount));
        if (agentDoneMetric != null) agentDoneMetric.setText(String.valueOf(doneCount));
        if (agentModeMetric != null) agentModeMetric.setText(activeCount > 0 ? "BUSY" : "LIVE");
        if (TAB_AGENT.equals(currentTab)) {
            setWarpState(activeCount > 0 ? "●  АБАДДОН / В РАБОТЕ" : "●  АБАДДОН / МОНИТОР", activeCount > 0 ? WARP : CYAN);
        }
        for (int i = length - 1; i >= 0; i--) {
            JSONObject task = tasks.optJSONObject(i);
            if (task == null || !agentTaskMatchesBrigade(task)) {
                continue;
            }
            String taskId = task.optString("task_id", "").trim();
            if (taskId.isEmpty()) {
                continue;
            }
            visibleTaskIds.add(taskId);
            visible++;
            AgentSection section = agentSections.get(taskId);
            if (section == null) {
                section = new AgentSection();
                section.container = new LinearLayout(this);
                section.container.setOrientation(LinearLayout.VERTICAL);
                section.cardHost = new LinearLayout(this);
                section.cardHost.setOrientation(LinearLayout.VERTICAL);
                section.cardsHost = new LinearLayout(this);
                section.cardsHost.setOrientation(LinearLayout.VERTICAL);
                section.finalHost = new LinearLayout(this);
                section.finalHost.setOrientation(LinearLayout.VERTICAL);
                agentAppendTarget = section.container;
                String prompt = task.optString("task", "").trim();
                if (!prompt.isEmpty()) {
                    addAgentMessage(true, prompt, false);
                }
                agentAppendTarget = null;
                section.container.addView(section.cardHost, new LinearLayout.LayoutParams(-1, -2));
                section.container.addView(section.cardsHost, new LinearLayout.LayoutParams(-1, -2));
                section.container.addView(section.finalHost, new LinearLayout.LayoutParams(-1, -2));
                agentMessageList.addView(section.container, new LinearLayout.LayoutParams(-1, -2));
                agentSections.put(taskId, section);
            }
            JSONObject missionState = task.optJSONObject("mission_state");
            String cardKey = task.optString("status", "") + "|" + task.optBoolean("running", false)
                    + "|" + task.optString("current_step", "")
                    + "|" + (missionState == null ? "" : missionState.optString("user_visible_state", ""));
            if (!cardKey.equals(section.cardKey)) {
                section.cardKey = cardKey;
                section.cardHost.removeAllViews();
                agentAppendTarget = section.cardHost;
                addAgentTaskCard(task, false);
                agentAppendTarget = null;
            }
            JSONArray cards = task.optJSONArray("activity_cards");
            if (cards == null) {
                cards = task.optJSONArray("activity_entries");
            }
            int total = cards == null ? 0 : cards.length();
            boolean prefixIntact = total >= section.cardKeys.size();
            for (int j = 0; prefixIntact && j < section.cardKeys.size(); j++) {
                JSONObject entry = cards.optJSONObject(j);
                if (entry == null || !section.cardKeys.get(j).equals(agentActivityCardKey(entry))) {
                    prefixIntact = false;
                }
            }
            if (!prefixIntact) {
                section.cardsHost.removeAllViews();
                section.cardKeys.clear();
            }
            for (int j = section.cardKeys.size(); j < total; j++) {
                JSONObject entry = cards.optJSONObject(j);
                if (entry == null) {
                    continue;
                }
                agentAppendTarget = section.cardsHost;
                addAgentActivityEntry(entry, j, total, true);
                agentAppendTarget = null;
                section.cardKeys.add(agentActivityCardKey(entry));
            }
            String finalText = task.optString("final", "").trim();
            if (!finalText.equals(section.finalKey)) {
                section.finalKey = finalText;
                section.finalHost.removeAllViews();
            }
            if (!finalText.isEmpty() && section.finalHost.getChildCount() == 0) {
                agentAppendTarget = section.finalHost;
                addAgentFinalMessage(finalText, true);
                agentAppendTarget = null;
            }
        }
        java.util.Iterator<java.util.Map.Entry<String, AgentSection>> sectionIterator = agentSections.entrySet().iterator();
        while (sectionIterator.hasNext()) {
            java.util.Map.Entry<String, AgentSection> entry = sectionIterator.next();
            if (!visibleTaskIds.contains(entry.getKey())) {
                agentMessageList.removeView(entry.getValue().container);
                sectionIterator.remove();
            }
        }
        if (visible == 0 && agentMessageList.getChildCount() == 0) {
            String label = agentBrigadeLabel(agentBrigadeFilter);
            addAgentMessage(false, label.isEmpty() ? "Задач по варбандам нет." : "У варбанды " + label + " пока нет задач.", false);
        }
        maybeScrollAgentToBottom(false);
    }

    private String agentActivityCardKey(JSONObject entry) {
        return entry.optString("headline", "").hashCode() + "~" + entry.optString("status", "")
                + "~" + entry.optString("severity", "") + "~" + entry.optString("detail", "").hashCode();
    }

    private boolean agentTaskMatchesBrigade(JSONObject task) {
        String filter = agentBrigadeFilter == null ? "" : agentBrigadeFilter.trim();
        if (filter.isEmpty()) {
            return true;
        }
        String governor = task.optString("governor", "").trim();
        return governor.equalsIgnoreCase(filter) || agentBrigadeLabel(governor).equalsIgnoreCase(agentBrigadeLabel(filter));
    }

    private String agentTaskStatusLabel(JSONObject task) {
        JSONObject missionState = task == null ? null : task.optJSONObject("mission_state");
        String visibleState = missionState == null ? "" : missionState.optString("user_visible_state", "").trim();
        if ("final_ready".equals(visibleState)) {
            return "Готово";
        }
        if ("working".equals(visibleState)) {
            return "В работе";
        }
        if ("accepted".equals(visibleState)) {
            return "Принято";
        }
        if ("needs_user_or_operator_decision".equals(visibleState)) {
            return "Нужен выбор";
        }
        if ("cancelled".equals(visibleState)) {
            return "Остановлено";
        }
        if ("failed".equals(visibleState)) {
            return "Ошибка";
        }
        if (task.optBoolean("running", false)) {
            return "В работе";
        }
        if (task.optBoolean("cancelled", false)) {
            return "Остановлено";
        }
        if (task.optBoolean("success", false)) {
            return "Готово";
        }
        return "Требует внимания";
    }

    private int agentSeverityColor(String severity, String status) {
        String cleanSeverity = severity == null ? "" : severity.trim().toLowerCase();
        String cleanStatus = status == null ? "" : status.trim().toLowerCase();
        if ("error".equals(cleanSeverity) || "failed".equals(cleanStatus) || "blocked".equals(cleanStatus) || "preflight_failed".equals(cleanStatus)) {
            return Color.rgb(220, 91, 91);
        }
        if ("warning".equals(cleanSeverity) || "needs_revision".equals(cleanStatus) || "cancelled".equals(cleanStatus)) {
            return Color.rgb(229, 183, 82);
        }
        if ("completed".equals(cleanStatus) || "passed_with_warnings".equals(cleanStatus)) {
            return Color.rgb(73, 203, 145);
        }
        return CYAN;
    }

    private TextView agentSmallLabel(String text, int textColor) {
        TextView label = new TextView(this);
        label.setText(text);
        label.setTextColor(textColor);
        label.setTextSize(12);
        label.setSingleLine(true);
        label.setEllipsize(TextUtils.TruncateAt.END);
        label.setPadding(dp(8), dp(3), dp(8), dp(3));
        label.setBackground(pill(SURFACE_SOFT, Color.argb(150, Color.red(textColor), Color.green(textColor), Color.blue(textColor)), dp(13)));
        return label;
    }

    private void addAgentTaskCard(JSONObject task, boolean animate) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(12), dp(10), dp(12), dp(10));
        card.setBackground(pill(SURFACE_RAISED, LINE, dp(16)));
        card.setAlpha(animate ? 0f : 1f);
        card.setTranslationY(animate ? dp(10) : 0f);

        String governor = task.optString("governor", "").trim();
        String brigade = agentBrigadeLabel(governor);
        String taskId = task.optString("task_id", "").trim();
        String currentStep = task.optString("current_step", "").trim();
        String statusLabel = agentTaskStatusLabel(task);
        JSONObject missionState = task.optJSONObject("mission_state");
        String canonicalStatus = missionState == null ? "" : missionState.optString("status", "").trim();
        int statusColor = agentSeverityColor("", canonicalStatus.isEmpty() ? task.optString("status", "") : canonicalStatus);

        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setOrientation(LinearLayout.HORIZONTAL);
        TextView title = new TextView(this);
        title.setText(brigade.isEmpty() ? "Warband" : brigade);
        title.setTextColor(TEXT);
        title.setTextSize(16);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setSingleLine(true);
        title.setEllipsize(TextUtils.TruncateAt.END);
        top.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        top.addView(agentSmallLabel(statusLabel, statusColor), new LinearLayout.LayoutParams(-2, -2));
        card.addView(top, new LinearLayout.LayoutParams(-1, -2));

        if (!taskId.isEmpty()) {
            TextView id = new TextView(this);
            id.setText(taskId);
            id.setTextColor(TEXT_MUTED);
            id.setTextSize(12);
            id.setSingleLine(true);
            id.setEllipsize(TextUtils.TruncateAt.MIDDLE);
            id.setPadding(0, dp(4), 0, 0);
            card.addView(id, new LinearLayout.LayoutParams(-1, -2));
        }

        if (!currentStep.isEmpty()) {
            TextView step = new TextView(this);
            step.setText("Сейчас: " + currentStep);
            step.setTextColor(Color.rgb(218, 215, 228));
            step.setTextSize(14);
            step.setLineSpacing(dp(2), 1.0f);
            step.setPadding(0, dp(8), 0, 0);
            card.addView(step, new LinearLayout.LayoutParams(-1, -2));
        }

        addAgentCardView(card, animate);
    }

    private void addAgentActivityEntry(JSONObject entry, int index, int total, boolean animate) {
        String headline = entry.optString("headline", "").trim();
        String detail = entry.optString("detail", "").trim();
        String status = entry.optString("status", "").trim();
        String severity = entry.optString("severity", "").trim();
        String worker = entry.optString("worker", "").trim();
        String kind = entry.optString("kind", "").trim();
        int accent = agentSeverityColor(severity, status);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, 0, 0, 0);
        row.setAlpha(animate ? 0f : 1f);
        row.setTranslationY(animate ? dp(10) : 0f);

        TextView rail = new TextView(this);
        rail.setText("");
        rail.setBackgroundColor(accent);
        LinearLayout.LayoutParams railLp = new LinearLayout.LayoutParams(dp(4), -1);
        railLp.rightMargin = dp(8);
        row.addView(rail, railLp);

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(10), dp(8), dp(10), dp(8));
        card.setBackground(pill(SURFACE, LINE, dp(14)));

        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setOrientation(LinearLayout.HORIZONTAL);
        TextView title = new TextView(this);
        title.setText(headline.isEmpty() ? "Шаг " + (index + 1) : headline);
        title.setTextColor(TEXT);
        title.setTextSize(14);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setSingleLine(false);
        top.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        top.addView(agentSmallLabel((index + 1) + "/" + Math.max(total, 1), accent), new LinearLayout.LayoutParams(-2, -2));
        card.addView(top, new LinearLayout.LayoutParams(-1, -2));

        if (!detail.isEmpty()) {
            TextView body = new TextView(this);
            body.setText(detail);
            body.setTextColor(Color.rgb(218, 215, 228));
            body.setTextSize(13);
            body.setLineSpacing(dp(2), 1.0f);
            body.setPadding(0, dp(6), 0, 0);
            card.addView(body, new LinearLayout.LayoutParams(-1, -2));
        }

        String metaText = "";
        if (!worker.isEmpty()) {
            metaText = worker;
        } else if (!kind.isEmpty()) {
            metaText = kind;
        }
        if (!status.isEmpty()) {
            metaText = metaText.isEmpty() ? status : metaText + " · " + status;
        }
        if (!metaText.isEmpty()) {
            TextView meta = new TextView(this);
            meta.setText(metaText);
            meta.setTextColor(TEXT_MUTED);
            meta.setTextSize(11);
            meta.setSingleLine(true);
            meta.setEllipsize(TextUtils.TruncateAt.END);
            meta.setPadding(0, dp(6), 0, 0);
            card.addView(meta, new LinearLayout.LayoutParams(-1, -2));
        }

        row.addView(card, new LinearLayout.LayoutParams(0, -2, 1));
        addAgentCardView(row, animate);
    }

    private void addAgentFinalMessage(String finalText, boolean animate) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(12), dp(10), dp(12), dp(10));
        card.setBackground(pill(SURFACE_RAISED, ACID, dp(16)));
        card.setAlpha(animate ? 0f : 1f);
        card.setTranslationY(animate ? dp(10) : 0f);

        TextView title = new TextView(this);
        title.setText("Финальный ответ");
        title.setTextColor(ACID);
        title.setTextSize(14);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(title, new LinearLayout.LayoutParams(-1, -2));

        TextView body = new TextView(this);
        applyRichText(body, finalText);
        body.setTextColor(TEXT);
        body.setTextSize(14);
        body.setLineSpacing(dp(2), 1.0f);
        body.setPadding(0, dp(6), 0, 0);
        card.addView(body, new LinearLayout.LayoutParams(-1, -2));

        addAgentCardView(card, animate);
        if (animate) card.performHapticFeedback(HapticFeedbackConstants.CONFIRM);
    }

    private void addAgentCardView(View card, boolean animate) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels - dp(40), dp(620)),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = Gravity.LEFT;
        lp.topMargin = dp(5);
        lp.bottomMargin = dp(5);
        lp.leftMargin = dp(4);
        lp.rightMargin = dp(4);
        (agentAppendTarget != null ? agentAppendTarget : agentMessageList).addView(card, lp);
        if (animate) {
            card.animate()
                    .alpha(1f)
                    .translationY(0f)
                    .setDuration(210)
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
        }
        maybeScrollAgentToBottom(false);
    }

    private String agentBrigadeLabel(String governor) {
        String clean = governor == null ? "" : governor.trim();
        if (clean.equalsIgnoreCase("Warmaster") || clean.equalsIgnoreCase("Abaddon")) {
            return "Абаддон";
        }
        if (clean.equalsIgnoreCase("IskandarKhayon") || clean.equalsIgnoreCase("Iskandar") || clean.equalsIgnoreCase("Khayon")) {
            return "Искандар Хайон";
        }
        if (clean.equalsIgnoreCase("Ceraxia") || clean.equalsIgnoreCase("CeraxiaTheRed") || clean.equalsIgnoreCase("Mechanicum")) {
            return "Цераксия";
        }
        if (clean.equalsIgnoreCase("Moriana") || clean.equalsIgnoreCase("Pictorium")) {
            return "Мориана";
        }
        return clean;
    }

    private String pollAgentTaskUntilDone(String taskId) throws Exception {
        String finalMessage = "";
        int transportFailures = 0;
        while (true) {
            JSONObject snapshot;
            try {
                snapshot = requestAgentTaskSnapshot(taskId);
                transportFailures = 0;
            } catch (Exception transportError) {
                // Transient network drops must not detach the monitor: the task
                // keeps running server-side and we can just poll again.
                transportFailures++;
                if (transportFailures >= 40) {
                    throw transportError;
                }
                Thread.sleep(appInForeground ? 4000 : 10000);
                continue;
            }
            JSONObject finalEvent = snapshot.optJSONObject("final");
            JSONArray events = snapshot.optJSONArray("events");
            if (events != null) {
                int start = Math.max(0, Math.min(agentDisplayedEventCount, events.length()));
                for (int i = start; i < events.length(); i++) {
                    JSONObject event = events.optJSONObject(i);
                    if (event != null) {
                        main.post(() -> handleAgentEvent(event));
                    }
                }
                agentDisplayedEventCount = events.length();
            }
            if (finalEvent != null) {
                finalMessage = finalEvent.optString("message", "").trim();
                boolean cancelled = finalEvent.optBoolean("cancelled", false);
                if (cancelled && finalMessage.isEmpty()) {
                    return "Абаддон остановлен: задача отменена.";
                }
                return finalMessage.isEmpty() ? "Абаддон вернул пустой ответ." : finalMessage;
            }
            if (!snapshot.optBoolean("running", false)) {
                return finalMessage.isEmpty() ? "Абаддон завершился без финального сообщения." : finalMessage;
            }
            Thread.sleep(2000);
        }
    }

    private String requestAgentCancel(String taskId) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("task_id", taskId);
        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/warmaster/cancel");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        JSONObject json = new JSONObject(response);
        if (!json.optBoolean("ok", false)) {
            throw new IllegalStateException(json.optString("error", response));
        }
        return json.optString("message", "Отмена отправлена.");
    }

    private String requestAgentState() throws Exception {
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/warmaster/state");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        JSONObject json = new JSONObject(response);
        JSONObject state = json.optJSONObject("state");
        if (state == null) {
            throw new IllegalStateException("empty state payload");
        }
        StringBuilder out = new StringBuilder();
        out.append("State: ").append(state.optBoolean("busy", false) ? "busy" : "idle");
        String revision = state.optString("revision", "").trim();
        if (!revision.isEmpty()) {
            out.append("\nRevision: ").append(agentBrigadeLabel(revision));
        }
        out.append("\nUptime: ").append(state.optDouble("uptime_sec", 0.0)).append("s");
        out.append("\nОчередь: ").append(state.optInt("queued", 0));
        int cancelledTaskCount = state.optInt("cancelled_task_count", 0);
        if (cancelledTaskCount > 0) {
            out.append("\nCancel flags: ").append(cancelledTaskCount);
        }
        String currentTask = state.optString("current_task_id", "").trim();
        if (!currentTask.isEmpty()) {
            out.append("\nТекущая задача: ").append(currentTask);
            out.append("\nДлительность: ").append(state.optDouble("current_task_duration_sec", 0.0)).append("s");
        }
        String lastTask = state.optString("last_task_id", "").trim();
        if (!lastTask.isEmpty()) {
            out.append("\nПоследняя задача: ").append(lastTask);
            out.append("\nExit code: ").append(state.optString("last_exit_code", ""));
            out.append("\nLast duration: ").append(state.optDouble("last_duration_sec", 0.0)).append("s");
        }
        out.append("\nCompleted: ").append(state.optInt("completed", 0));
        return out.toString();
    }

    private void toggleWhisperRecording(String language, EditText output, String titleText, Button button) {
        if (recording) {
            recording = false;
            speechStatus.setText("Останавливаю запись...");
            return;
        }

        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            pendingSpeechLanguage = language;
            pendingSpeechOutput = output;
            pendingSpeechTitle = titleText;
            activeSpeechButton = button;
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_RECORD_AUDIO);
            return;
        }

        startWhisperRecording(language, output, titleText, button);
    }

    private void startWhisperRecording(String language, EditText output, String titleText, Button button) {
        activeSpeechOutput = output;
        activeSpeechButton = button;
        recording = true;
        if (button == chatVoiceButton) {
            button.setCompoundDrawablesWithIntrinsicBounds(0, 0, 0, 0);
            button.setText("■");
            button.setTextColor(Color.rgb(255, 115, 115));
            button.setContentDescription("Остановить запись");
        } else {
            button.setText("STOP");
        }
        speechStatus.setText(titleText + ": слушаю и сразу отправляю...");
        setWarpState("●  СЛУШАЮ / ГОВОРИ", CYAN);
        button.performHapticFeedback(HapticFeedbackConstants.CONFIRM);

        new Thread(() -> runStreamingRemoteStt(language, titleText)).start();
    }

    private void runStreamingRemoteStt(String language, String titleText) {
        try {
            String result = requestRemoteSttLive(language, titleText).trim();
            main.post(() -> {
                resetSpeechButton();
                if (activeSpeechOutput != null) {
                    activeSpeechOutput.setText(result.isEmpty() ? "" : result);
                    activeSpeechOutput.setSelection(activeSpeechOutput.getText().length());
                }
                if (!result.isEmpty() && activeSpeechOutput == translatorSourceText) {
                    speechStatus.setText("Распознано. Перевожу...");
                    translateCurrentText();
                } else {
                    speechStatus.setText("Готово.");
                }
                restoreHeaderState();
            });
        } catch (Exception exc) {
            main.post(() -> {
                resetSpeechButton();
                speechStatus.setText("STT ошибка: " + exc.getMessage());
                if (TAB_CHAT.equals(currentTab) || TAB_TRANSLATOR.equals(currentTab)) {
                    setWarpState("●  ОШИБКА ГОЛОСА", Color.rgb(220, 91, 91));
                }
            });
        }
    }

    private String requestRemoteSttLive(String language, String titleText) throws Exception {
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/stt-live");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(240000);
        conn.setDoOutput(true);
        conn.setChunkedStreamingMode(4096);
        conn.setRequestProperty("Content-Type", "application/octet-stream");
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("X-Language", language);
        conn.setRequestProperty("X-Sample-Rate", String.valueOf(AUDIO_SAMPLE_RATE));
        applyMobileAuth(conn);

        int minBuffer = AudioRecord.getMinBufferSize(
                AUDIO_SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        int bufferSize = Math.max(minBuffer, AUDIO_SAMPLE_RATE / 2);
        AudioRecord recorder = new AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                AUDIO_SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize);
        short[] buffer = new short[Math.max(1024, bufferSize / 2)];
        byte[] pcm = new byte[buffer.length * 2];
        int bytesSent = 0;

        try (OutputStream out = conn.getOutputStream()) {
            recorder.startRecording();
            while (recording) {
                int read = recorder.read(buffer, 0, buffer.length);
                if (read <= 0) {
                    continue;
                }
                for (int i = 0; i < read; i++) {
                    pcm[i * 2] = (byte) (buffer[i] & 0xff);
                    pcm[i * 2 + 1] = (byte) ((buffer[i] >> 8) & 0xff);
                }
                out.write(pcm, 0, read * 2);
                bytesSent += read * 2;
            }
            out.flush();
        } finally {
            try {
                recorder.stop();
            } catch (Exception ignored) {
            }
            recorder.release();
        }

        if (bytesSent < AUDIO_SAMPLE_RATE) {
            throw new IllegalStateException("Слишком коротко.");
        }
        main.post(() -> speechStatus.setText(titleText + ": аудио уже на сервере, распознаю..."));
        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        return new JSONObject(response).optString("text", "").trim();
    }

    private String requestRemoteStt(String language, float[] audio) throws Exception {
        ByteArrayOutputStream pcm = new ByteArrayOutputStream(audio.length * 2);
        for (float sample : audio) {
            int value = Math.max(Short.MIN_VALUE, Math.min(Short.MAX_VALUE, Math.round(sample * 32767f)));
            pcm.write(value & 0xff);
            pcm.write((value >> 8) & 0xff);
        }

        byte[] body = pcm.toByteArray();
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/stt-pcm");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(240000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/octet-stream");
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("X-Language", language);
        conn.setRequestProperty("X-Sample-Rate", String.valueOf(AUDIO_SAMPLE_RATE));
        applyMobileAuth(conn);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        return new JSONObject(response).optString("text", "").trim();
    }

    private void resetSpeechButton() {
        if (activeSpeechButton != null) {
            if (activeSpeechButton == chatVoiceButton) {
                activeSpeechButton.setText("");
                activeSpeechButton.setCompoundDrawablesWithIntrinsicBounds(R.drawable.ic_mic, 0, 0, 0);
                activeSpeechButton.setCompoundDrawableTintList(android.content.res.ColorStateList.valueOf(CYAN));
                activeSpeechButton.setTextColor(CYAN);
                activeSpeechButton.setContentDescription("Голосовой ввод");
            } else {
                activeSpeechButton.setText(activeSpeechButton == speechButton
                        ? "REC " + TRANSLATOR_SHORT[translatorSourceIndex] : "REC");
            }
        }
        activeSpeechButton = null;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_RECORD_AUDIO) {
            if (grantResults.length > 0
                    && grantResults[0] == PackageManager.PERMISSION_GRANTED
                    && pendingSpeechLanguage != null
                    && pendingSpeechOutput != null
                    && activeSpeechButton != null) {
                startWhisperRecording(pendingSpeechLanguage, pendingSpeechOutput, pendingSpeechTitle, activeSpeechButton);
            } else {
                speechStatus.setText("Микрофон не разрешен.");
            }
            pendingSpeechLanguage = null;
            pendingSpeechOutput = null;
            pendingSpeechTitle = null;
        } else if (requestCode == REQUEST_READ_IMAGES) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                showImagePicker();
            } else {
                addMessage(false, "Доступ к изображениям не разрешен.");
            }
        }
    }

    private void buildDrawer(FrameLayout root) {
        scrim = new View(this);
        scrim.setBackgroundColor(Color.argb(205, 0, 0, 0));
        scrim.setAlpha(0f);
        scrim.setVisibility(View.GONE);
        root.addView(scrim, new FrameLayout.LayoutParams(-1, -1));
        scrim.setOnClickListener(v -> setDrawerOpen(false));

        int drawerWidth = Math.min(dp(316), getResources().getDisplayMetrics().widthPixels - dp(52));
        drawer = new LinearLayout(this);
        drawer.setOrientation(LinearLayout.VERTICAL);
        drawer.setPadding(dp(20), dp(34), dp(20), dp(20));
        drawer.setBackground(drawerBackground());
        drawer.setTranslationX(-drawerWidth);
        FrameLayout.LayoutParams drawerLp = new FrameLayout.LayoutParams(drawerWidth, -1, Gravity.LEFT);
        root.addView(drawer, drawerLp);

        TextView name = new TextView(this);
        name.setText("Шушуня");
        name.setTextColor(TEXT);
        name.setTextSize(28);
        name.setTypeface(Typeface.DEFAULT_BOLD);
        name.setLetterSpacing(-0.02f);
        drawer.addView(name, new LinearLayout.LayoutParams(-1, dp(44)));

        TextView signature = new TextView(this);
        signature.setText("ПЕРСОНАЛЬНЫЙ ДЕМОН • ONLINE");
        signature.setTextColor(CYAN);
        signature.setTextSize(10);
        signature.setTypeface(Typeface.DEFAULT_BOLD);
        signature.setLetterSpacing(0.10f);
        LinearLayout.LayoutParams signatureLp = new LinearLayout.LayoutParams(-1, dp(34));
        signatureLp.bottomMargin = dp(14);
        drawer.addView(signature, signatureLp);

        drawerChat = drawerItem("Шушуня");
        drawerTranslator = drawerItem("Переводчик");
        drawerAgent = drawerItem("Warbands");
        drawerMemory = drawerItem("Память");
        drawer.addView(drawerChat);
        drawer.addView(drawerTranslator);
        drawer.addView(drawerAgent);
        drawer.addView(drawerMemory);

        drawerChat.setOnClickListener(v -> {
            showTab(TAB_CHAT);
            setDrawerOpen(false);
        });
        drawerTranslator.setOnClickListener(v -> {
            showTab(TAB_TRANSLATOR);
            setDrawerOpen(false);
        });
        drawerAgent.setOnClickListener(v -> {
            showTab(TAB_AGENT);
            setDrawerOpen(false);
        });
        drawerMemory.setOnClickListener(v -> {
            showTab(TAB_MEMORY);
            setDrawerOpen(false);
        });
        updateDrawerSelection();
    }

    private TextView drawerItem(String text) {
        TextView item = new TextView(this);
        item.setText(text);
        item.setTextSize(16);
        item.setTypeface(Typeface.DEFAULT_BOLD);
        item.setGravity(Gravity.CENTER_VERTICAL);
        item.setPadding(dp(18), 0, dp(18), 0);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(52));
        lp.topMargin = dp(8);
        item.setLayoutParams(lp);
        return item;
    }

    private void showTab(String tab) {
        currentTab = tab;
        boolean chat = TAB_CHAT.equals(tab);
        boolean translator = TAB_TRANSLATOR.equals(tab);
        boolean agent = TAB_AGENT.equals(tab);
        boolean memory = TAB_MEMORY.equals(tab);
        title.setText(chat ? "Шушуня" : agent ? "Warbands" : memory ? "Память" : "Переводчик");
        endpoint.setVisibility(View.VISIBLE);
        chatView.setVisibility(chat ? View.VISIBLE : View.GONE);
        translatorView.setVisibility(translator ? View.VISIBLE : View.GONE);
        agentView.setVisibility(agent ? View.VISIBLE : View.GONE);
        memoryView.setVisibility(memory ? View.VISIBLE : View.GONE);
        if (chat) {
            if (messageList == null || messageList.getChildCount() == 0) {
                loadServerChatHistory();
            } else {
                startChatDeltaLoop();
            }
            translatorView.setPadding(0, dp(10), 0, 0);
            agentView.setPadding(0, dp(6), 0, 0);
            scrollView.setPadding(0, 0, 0, 0);
            inputPanel.setTranslationY(0f);
        } else if (agent) {
            inputPanel.setTranslationY(0f);
            scrollView.setPadding(0, 0, 0, 0);
            updateAgentKeyboardLift();
            refreshBrigadeMonitor();
            startBrigadeDeltaLoop();
        } else if (translator) {
            inputPanel.setTranslationY(0f);
            scrollView.setPadding(0, 0, 0, 0);
            updateToolKeyboardPadding();
        } else {
            inputPanel.setTranslationY(0f);
            scrollView.setPadding(0, 0, 0, 0);
            loadMemoryDashboard();
        }
        updateDrawerSelection();
        updateBottomNavigation();
        restoreHeaderState();
        if (appRoot != null) appRoot.requestApplyInsets();
    }

    private void updateToolKeyboardPadding() {
        if (translatorView != null) {
            translatorView.setPadding(0, dp(10), 0, 0);
        }
    }

    private void updateAgentKeyboardLift() {
        if (agentScrollView == null) {
            return;
        }
        agentScrollView.post(() -> {
            agentScrollView.setPadding(0, 0, 0, 0);
            maybeScrollAgentToBottom(false);
        });
    }

    private void updateDrawerSelection() {
        styleDrawerItem(drawerChat, TAB_CHAT.equals(currentTab));
        styleDrawerItem(drawerTranslator, TAB_TRANSLATOR.equals(currentTab));
        styleDrawerItem(drawerAgent, TAB_AGENT.equals(currentTab));
        styleDrawerItem(drawerMemory, TAB_MEMORY.equals(currentTab));
    }

    private void styleDrawerItem(TextView item, boolean selected) {
        item.setTextColor(selected ? INK : TEXT_MUTED);
        item.setBackground(selected
                ? pill(ACID, ACID, dp(17))
                : pill(SURFACE_RAISED, LINE, dp(17)));
    }

    private void setDrawerOpen(boolean open) {
        drawerOpen = open;
        if (open) {
            scrim.setVisibility(View.VISIBLE);
        }
        float target = open ? 0f : -drawer.getWidth();
        drawer.animate().translationX(target).setDuration(220).setInterpolator(new DecelerateInterpolator()).start();
        scrim.animate()
                .alpha(open ? 1f : 0f)
                .setDuration(190)
                .withEndAction(() -> {
                    if (!drawerOpen) {
                        scrim.setVisibility(View.GONE);
                    }
                })
                .start();
    }

    private void pickImage() {
        if (checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.READ_MEDIA_IMAGES}, REQUEST_READ_IMAGES);
            return;
        }
        showImagePicker();
    }

    private void showImagePicker() {
        ArrayList<Uri> images = recentImageUris();
        if (images.isEmpty()) {
            addMessage(false, "Изображения не найдены или доступ ограничен.");
            return;
        }

        GridView grid = new GridView(this);
        grid.setNumColumns(3);
        grid.setStretchMode(GridView.STRETCH_COLUMN_WIDTH);
        grid.setVerticalSpacing(dp(8));
        grid.setHorizontalSpacing(dp(8));
        grid.setPadding(dp(10), dp(10), dp(10), dp(10));
        grid.setClipToPadding(false);
        grid.setBackgroundColor(INK);
        ImageGridAdapter adapter = new ImageGridAdapter(images);
        grid.setAdapter(adapter);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Выбери картинку")
                .setView(grid)
                .setNegativeButton("Отмена", null)
                .create();
        grid.setOnItemClickListener((parent, view, position, id) -> {
            dialog.dismiss();
            attachImage.animate().alpha(0.55f).setDuration(120).start();
            new Thread(() -> loadPendingImage(images.get(position))).start();
        });
        dialog.setOnShowListener(d -> {
            Window window = dialog.getWindow();
            if (window != null) {
                window.setBackgroundDrawable(pill(SURFACE, WARP, dp(18)));
            }
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setTextColor(ACID);
        });
        dialog.show();
    }

    private ArrayList<Uri> recentImageUris() {
        ArrayList<Uri> images = new ArrayList<>();
        String[] projection = new String[]{MediaStore.Images.Media._ID};
        Bundle args = new Bundle();
        args.putStringArray(ContentResolver.QUERY_ARG_SORT_COLUMNS, new String[]{MediaStore.Images.Media.DATE_ADDED});
        args.putInt(ContentResolver.QUERY_ARG_SORT_DIRECTION, ContentResolver.QUERY_SORT_DIRECTION_DESCENDING);
        args.putInt(ContentResolver.QUERY_ARG_LIMIT, 90);

        try (Cursor cursor = getContentResolver().query(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                projection,
                args,
                null)) {
            if (cursor == null) {
                return images;
            }
            int idColumn = cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID);
            while (cursor.moveToNext()) {
                long id = cursor.getLong(idColumn);
                images.add(ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id));
            }
        } catch (Exception ignored) {
        }
        return images;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_PICK_IMAGE && resultCode == RESULT_OK && data != null && data.getData() != null) {
            Uri uri = data.getData();
            try {
                getContentResolver().takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } catch (Exception ignored) {
            }
            attachImage.animate().alpha(0.55f).setDuration(120).start();
            new Thread(() -> loadPendingImage(uri)).start();
        }
    }

    private void loadPendingImage(Uri uri) {
        try {
            String mime = getContentResolver().getType(uri);
            byte[] raw;
            try (InputStream in = getContentResolver().openInputStream(uri)) {
                raw = readBytes(in);
            }
            byte[] encodedBytes = compressImageForChat(raw);
            String dataUrl = "data:image/jpeg;base64," + Base64.encodeToString(encodedBytes, Base64.NO_WRAP);
            String label = "Картинка, " + Math.max(1, encodedBytes.length / 1024) + " КБ";
            if (mime != null && mime.toLowerCase().contains("png") && encodedBytes == raw) {
                dataUrl = "data:image/png;base64," + Base64.encodeToString(encodedBytes, Base64.NO_WRAP);
            }
            final String finalDataUrl = dataUrl;
            final String finalLabel = label;
            final Bitmap finalPreview = BitmapFactory.decodeByteArray(encodedBytes, 0, encodedBytes.length);
            main.post(() -> {
                pendingImageDataUrl = finalDataUrl;
                pendingImageLabel = finalLabel;
                pendingImagePreview = finalPreview;
                selectedImagePreview.setImageBitmap(finalPreview);
                selectedImagePreviewHost.setVisibility(View.VISIBLE);
                updateComposerActions();
                updateChatKeyboardLift();
                attachImage.animate().alpha(1f).setDuration(120).start();
                attachImage.setColorFilter(ACID);
            });
        } catch (Exception exc) {
            main.post(() -> {
                pendingImageDataUrl = null;
                pendingImageLabel = null;
                pendingImagePreview = null;
                selectedImagePreviewHost.setVisibility(View.GONE);
                updateChatKeyboardLift();
                resetAttachImageButton();
                addMessage(false, "Картинку не удалось подготовить: " + exc.getMessage());
            });
        }
    }

    private void clearPendingImage() {
        pendingImageDataUrl = null;
        pendingImageLabel = null;
        pendingImagePreview = null;
        selectedImagePreview.setImageDrawable(null);
        selectedImagePreviewHost.setVisibility(View.GONE);
        updateComposerActions();
        updateChatKeyboardLift();
        resetAttachImageButton();
    }

    private byte[] compressImageForChat(byte[] raw) {
        Bitmap bitmap = BitmapFactory.decodeByteArray(raw, 0, raw.length);
        if (bitmap == null) {
            return raw;
        }
        int width = bitmap.getWidth();
        int height = bitmap.getHeight();
        int maxSide = Math.max(width, height);
        Bitmap output = bitmap;
        if (maxSide > 1280) {
            float scale = 1280f / maxSide;
            int outWidth = Math.max(1, Math.round(width * scale));
            int outHeight = Math.max(1, Math.round(height * scale));
            output = Bitmap.createScaledBitmap(bitmap, outWidth, outHeight, true);
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        output.compress(Bitmap.CompressFormat.JPEG, 86, out);
        if (output != bitmap) {
            output.recycle();
        }
        bitmap.recycle();
        return out.toByteArray();
    }

    private GradientDrawable makeBackground() {
        return new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{
                        Color.rgb(18, 13, 31),
                        INK,
                        Color.rgb(8, 9, 18)
                });
    }

    private GradientDrawable drawerBackground() {
        GradientDrawable drawable = new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{
                        Color.rgb(25, 20, 39),
                        SURFACE
                });
        drawable.setStroke(dp(1), LINE);
        return drawable;
    }

    private GradientDrawable gradientPill(int start, int end, int stroke, int radius) {
        GradientDrawable drawable = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{start, end});
        drawable.setCornerRadius(radius);
        drawable.setStroke(dp(1), stroke);
        return drawable;
    }

    private GradientDrawable pill(int color, int stroke, int radius) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(radius);
        drawable.setStroke(dp(1), stroke);
        return drawable;
    }

    private void submit() {
        String text = input.getText().toString().trim();
        String imageDataUrl = pendingImageDataUrl;
        String imageLabel = pendingImageLabel;
        Bitmap imagePreview = pendingImagePreview;
        boolean hasImage = imageDataUrl != null && !imageDataUrl.isEmpty();
        if ((text.isEmpty() && !hasImage) || waiting) {
            return;
        }
        send.performHapticFeedback(HapticFeedbackConstants.CONFIRM);
        String warmasterTask = hasImage ? "" : warmasterTaskFromChatCommand(text);
        if (!warmasterTask.isEmpty()) {
            input.setText("");
            runWarmasterTaskFromChat(text, warmasterTask);
            return;
        }

        input.setText("");
        pendingImageDataUrl = null;
        pendingImageLabel = null;
        pendingImagePreview = null;
        selectedImagePreview.setImageDrawable(null);
        selectedImagePreviewHost.setVisibility(View.GONE);
        resetAttachImageButton();
        if (hasImage) {
            addImageMessage(text, imagePreview, imageLabel, -1);
            maybeScrollToBottom(true);
            pendingLocalEchoes.addLast("user\n" + text + "\n[image attached server-side]");
        } else {
            addMessage(true, text);
            pendingLocalEchoes.addLast("user\n" + text);
        }
        TextView answerBubble = addMessage(false, "", false);
        pendingAnswerBubble = answerBubble;
        setWaiting(true);
        new Thread(() -> {
            PowerManager.WakeLock wakeLock = acquireAnswerWakeLock();
            try {
                StreamingBubble liveBubble = new StreamingBubble(answerBubble);
                liveBubble.start();
                String finalText = streamChatAnswer(text, imageDataUrl, liveBubble);
                pendingLocalEchoes.addLast("assistant\n" + finalText);
                main.post(() -> {
                    if (pendingAnswerBubble == answerBubble) {
                        pendingAnswerBubble = null;
                    }
                });
                liveBubble.finish();
                showAnswerNotification(finalText);
                main.post(() -> setWaiting(false));
            } catch (Exception e) {
                // No manual recovery needed: the delta stream is the recovery.
                // The answer lands server-side and the delta loop fills this bubble.
                main.post(() -> {
                    answerBubble.setText("⏳ связь моргнула — ответ доедет сюда сам");
                    setWaiting(false);
                });
            } finally {
                if (wakeLock != null && wakeLock.isHeld()) {
                    wakeLock.release();
                }
            }
        }).start();
    }

    private String warmasterTaskFromChatCommand(String text) {
        String clean = text == null ? "" : text.trim();
        if (clean.isEmpty()) {
            return "";
        }
        String lower = clean.toLowerCase();
        String[] prefixes = {"/task ", "/w ", "/abaddon ", "!task ", "!абаддон ", "/warmaster ", "!вармастер "};
        for (String prefix : prefixes) {
            if (lower.startsWith(prefix)) {
                return clean.substring(prefix.length()).trim();
            }
        }
        String[] colonPrefixes = {"абаддон:", "abaddon:", "вармастер:", "warmaster:"};
        for (String prefix : colonPrefixes) {
            if (lower.startsWith(prefix)) {
                return clean.substring(prefix.length()).trim();
            }
        }
        return "";
    }

    private void runWarmasterTaskFromChat(String originalText, String task) {
        String clean = task == null ? "" : task.trim();
        if (clean.isEmpty()) {
            addMessage(false, "После команды Абаддона укажи саму задачу.");
            return;
        }
        if (agentRunning) {
            addMessage(false, "Абаддон уже выполняет задачу. Открой вкладку Warbands и дождись завершения или отмени текущую.");
            return;
        }

        addMessage(true, originalText == null || originalText.trim().isEmpty() ? clean : originalText.trim());
        TextView answerBubble = addMessage(false, "Абаддон принимает задачу...", false);
        setWaiting(true);
        String taskId = "client-" + System.currentTimeMillis();
        currentAgentTaskId = taskId;
        agentDisplayedEventCount = 0;
        agentCancelRequested = false;
        agentRunning = true;
        if (agentStatus != null) {
            agentStatus.setText("Абаддон выполняет задачу из основного чата...");
        }
        setAgentRunButtonRunning(true);

        new Thread(() -> {
            PowerManager.WakeLock wakeLock = acquireAnswerWakeLock();
            String acceptedTaskId = taskId;
            final String[] acceptedTaskIdRef = {""};
            try {
                acceptedTaskId = requestAgentStart(clean, taskId);
                acceptedTaskIdRef[0] = acceptedTaskId;
                currentAgentTaskId = acceptedTaskId;
                getSharedPreferences(PREFS, MODE_PRIVATE)
                        .edit()
                        .putString("current_agent_task_id", acceptedTaskId)
                        .apply();
                String acceptedMessage = warmasterAcceptedChatMessage(acceptedTaskId);
                main.post(() -> {
                    applyRichText(answerBubble, acceptedMessage);
                    saveChatMessage(false, acceptedMessage);
                    maybeScrollToBottom(false);
                    refreshBrigadeMonitor();
                });
                // No live-watch loop: the app must not depend on a standing
                // connection. The result returns through the pending-reports
                // outbox (badge/notification); progress is in the Brigades tab.
                String finishedTaskId = acceptedTaskId;
                main.post(() -> {
                    agentRunning = false;
                    agentCancelRequested = false;
                    currentAgentTaskId = "";
                    getSharedPreferences(PREFS, MODE_PRIVATE)
                            .edit()
                            .remove("current_agent_task_id")
                            .apply();
                    setWaiting(false);
                    setAgentRunButtonRunning(false);
                    if (agentStatus != null) {
                        agentStatus.setText("Абаддон ведёт задачу " + finishedTaskId + "; результат придёт докладом.");
                    }
                });
            } catch (Exception exc) {
                String acceptedForCatch = acceptedTaskIdRef[0];
                boolean accepted = !acceptedForCatch.isEmpty();
                String message = accepted
                        ? warmasterMonitorDetachedMessage(acceptedForCatch)
                        : warmasterSubmitFailedMessage();
                main.post(() -> {
                    agentRunning = false;
                    agentCancelRequested = false;
                    if (accepted) {
                        currentAgentTaskId = acceptedForCatch;
                        getSharedPreferences(PREFS, MODE_PRIVATE)
                                .edit()
                                .putString("current_agent_task_id", acceptedForCatch)
                                .apply();
                    } else {
                        currentAgentTaskId = "";
                        getSharedPreferences(PREFS, MODE_PRIVATE)
                                .edit()
                                .remove("current_agent_task_id")
                                .apply();
                    }
                    setWaiting(false);
                    setAgentRunButtonRunning(false);
                    applyRichText(answerBubble, message);
                    saveChatMessage(false, message);
                    showAnswerNotification(message);
                    if (agentStatus != null) {
                        agentStatus.setText(message);
                    }
                    maybeScrollToBottom(false);
                });
            } finally {
                if (wakeLock != null && wakeLock.isHeld()) {
                    wakeLock.release();
                }
            }
        }).start();
    }

    private String warmasterAcceptedChatMessage(String taskId) {
        String cleanTaskId = taskId == null ? "" : taskId.trim();
        StringBuilder out = new StringBuilder();
        out.append("Абаддон принял задачу.");
        if (!cleanTaskId.isEmpty()) {
            out.append("\n").append("task_id=").append(cleanTaskId);
        }
        out.append("\n\nХод работы открыт во вкладке Warbands; основной чат получит только финал или запрос твоего решения.");
        return out.toString();
    }

    private void resetAttachImageButton() {
        attachImage.animate().alpha(1f).setDuration(120).start();
        attachImage.setColorFilter(TEXT_MUTED);
    }

    private void streamAnswer(String text, String imageDataUrl, StreamingBubble liveBubble) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("session_id", SERVER_CHAT_SESSION_ID);
        payload.put("model", MODEL);
        payload.put("user", SERVER_CHAT_SESSION_ID);
        payload.put("archive_enabled", true);
        payload.put("focus_enabled", true);
        payload.put("memory_namespace", SERVER_MEMORY_NAMESPACE);
        payload.put("client_source", "app");
        payload.put("max_tokens", 2048);
        payload.put("temperature", 0.4);
        payload.put("stream", true);
        payload.put("system_prompt", SYSTEM_PROMPT);
        payload.put("text", text);
        if (imageDataUrl != null && !imageDataUrl.isEmpty()) {
            payload.put("image_data_url", imageDataUrl);
        }

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/chat/completions");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(180000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "text/event-stream");
        applyMobileAuth(conn);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            InputStream stream = conn.getErrorStream();
            String response = readAll(stream);
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }

        streamingAnswer = true;
        liveBubble.start();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                if (!line.startsWith("data:")) {
                    continue;
                }
                String data = line.substring(5).trim();
                if ("[DONE]".equals(data)) {
                    break;
                }
                String delta = streamDelta(data);
                if (delta.isEmpty()) {
                    continue;
                }
                liveBubble.append(delta);
            }
        }
        streamingAnswer = false;
        liveBubble.finish();
        String finalText = liveBubble.targetText();
        saveChatMessage(false, finalText);
        showAnswerNotification(finalText);
    }

    private String streamChatAnswer(String text, String imageDataUrl, StreamingBubble liveBubble) throws Exception {
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/chat/stream");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(180000);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "text/event-stream");
        applyMobileAuth(conn);
        JSONObject payload = new JSONObject();
        payload.put("session_id", SERVER_CHAT_SESSION_ID);
        payload.put("text", text);
        payload.put("client_source", "app");
        if (imageDataUrl != null && !imageDataUrl.isEmpty()) {
            payload.put("image_data_url", imageDataUrl);
        }
        try (OutputStream os = conn.getOutputStream()) {
            os.write(payload.toString().getBytes(StandardCharsets.UTF_8));
        }
        if (conn.getResponseCode() < 200 || conn.getResponseCode() >= 300) {
            throw new IllegalStateException("stream http " + conn.getResponseCode());
        }
        StringBuilder full = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (!line.startsWith("data:")) {
                    continue;
                }
                String data = line.substring(5).trim();
                if (data.isEmpty()) {
                    continue;
                }
                JSONObject evt = new JSONObject(data);
                String type = evt.optString("type", "");
                if ("token".equals(type)) {
                    String piece = evt.optString("text", "");
                    full.append(piece);
                    liveBubble.append(piece);
                } else if ("route".equals(type)) {
                    // The turn controller sent this to the brigade; the mission is
                    // already running server-side. Surface an ack; progress shows
                    // in the Brigades tab and the delta stream.
                    String ack = "Взял в работу — веду через варбанду. Прогресс во вкладке Warbands.";
                    full.setLength(0);
                    full.append(ack);
                    liveBubble.append(ack);
                } else if ("error".equals(type)) {
                    throw new IllegalStateException(evt.optString("error", "stream error"));
                } else if ("done".equals(type)) {
                    String f = evt.optString("full", "");
                    if (full.length() == 0 && !f.isEmpty()) {
                        full.append(f);
                        liveBubble.append(f);
                    }
                    break;
                }
            }
        } finally {
            conn.disconnect();
        }
        return full.toString();
    }

    private String requestChatStart(String text, String imageDataUrl) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("session_id", SERVER_CHAT_SESSION_ID);
        payload.put("model", MODEL);
        payload.put("user", SERVER_CHAT_SESSION_ID);
        payload.put("archive_enabled", true);
        payload.put("focus_enabled", true);
        payload.put("memory_namespace", SERVER_MEMORY_NAMESPACE);
        payload.put("client_source", "app");
        payload.put("max_tokens", 2048);
        payload.put("temperature", 0.4);
        payload.put("stream", false);
        payload.put("system_prompt", SYSTEM_PROMPT);
        payload.put("text", text);
        if (imageDataUrl != null && !imageDataUrl.isEmpty()) {
            payload.put("image_data_url", imageDataUrl);
        }

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/chat/start");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        JSONObject json = new JSONObject(response);
        if (!json.optBoolean("ok", false)) {
            throw new IllegalStateException(json.optString("error", response));
        }
        return json.optString("job_id", "");
    }

    private JSONObject requestMobileJobSnapshot(String jobId) throws Exception {
        URL url = new URL(trimSlash(baseUrl) + "/archive/client/job?job_id=" + jobId);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("Accept", "application/json");
        applyMobileAuth(conn);

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        return new JSONObject(response);
    }

    private String pollChatJobUntilDone(String jobId) throws Exception {
        if (jobId == null || jobId.trim().isEmpty()) {
            throw new IllegalStateException("empty chat job id");
        }
        int transportFailures = 0;
        while (true) {
            JSONObject snapshot;
            try {
                snapshot = requestMobileJobSnapshot(jobId);
                transportFailures = 0;
            } catch (Exception transportError) {
                // One dropped poll must not kill the wait: the server finishes
                // the turn and persists the answer regardless of our connection.
                transportFailures++;
                if (transportFailures >= 40) {
                    throw new IllegalStateException("connection lost while waiting for the answer", transportError);
                }
                Thread.sleep(appInForeground ? 3000 : 8000);
                continue;
            }
            String status = snapshot.optString("status", "");
            if ("done".equals(status)) {
                JSONObject response = snapshot.optJSONObject("response");
                if (response == null) {
                    return "Сервер завершил чат без ответа.";
                }
                String message = response.optString("message", "").trim();
                if (!message.isEmpty()) {
                    return message;
                }
                JSONObject llm = response.optJSONObject("response");
                if (llm != null) {
                    JSONArray choices = llm.optJSONArray("choices");
                    if (choices != null && choices.length() > 0) {
                        JSONObject choice = choices.optJSONObject(0);
                        JSONObject msg = choice == null ? null : choice.optJSONObject("message");
                        if (msg != null) {
                            message = msg.optString("content", "").trim();
                        }
                    }
                }
                return message.isEmpty() ? "Сервер вернул пустой ответ." : message;
            }
            if ("failed".equals(status)) {
                throw new IllegalStateException(snapshot.optString("error", "chat job failed"));
            }
            Thread.sleep(1200);
        }
    }

    private void applyMobileAuth(HttpURLConnection conn) {
        conn.setRequestProperty("User-Agent", CLIENT_USER_AGENT);
        String apiKey = BuildConfig.CLIENT_API_KEY == null ? "" : BuildConfig.CLIENT_API_KEY.trim();
        if (!apiKey.isEmpty()) {
            conn.setRequestProperty("Authorization", "Bearer " + apiKey);
            conn.setRequestProperty("X-Shushunya-Client-Key", apiKey);
        }
    }

    private String streamDelta(String data) {
        try {
            JSONArray choices = new JSONObject(data).optJSONArray("choices");
            if (choices == null || choices.length() == 0) {
                return "";
            }
            JSONObject choice = choices.getJSONObject(0);
            JSONObject delta = choice.optJSONObject("delta");
            JSONObject message = choice.optJSONObject("message");
            String content = null;
            if (delta != null && !delta.isNull("content")) {
                content = delta.optString("content", "");
            }
            if ((content == null || content.isEmpty()) && message != null && !message.isNull("content")) {
                content = message.optString("content", "");
            }
            return content == null ? "" : content;
        } catch (Exception ignored) {
            return "";
        }
    }

    private Object userContent(String text, String imageDataUrl) throws Exception {
        if (imageDataUrl == null || imageDataUrl.isEmpty()) {
            return text;
        }
        JSONArray content = new JSONArray();
        content.put(new JSONObject()
                .put("type", "text")
                .put("text", text == null || text.trim().isEmpty()
                        ? "Посмотри картинку и ответь по ней."
                        : text));
        content.put(new JSONObject()
                .put("type", "image_url")
                .put("image_url", new JSONObject().put("url", imageDataUrl)));
        return content;
    }

    private String readAll(InputStream stream) throws Exception {
        if (stream == null) {
            return "";
        }
        StringBuilder out = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                out.append(line);
            }
        }
        return out.toString();
    }

    private byte[] readBytes(InputStream stream) throws Exception {
        if (stream == null) {
            throw new IllegalStateException("empty stream");
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[16384];
        int read;
        while ((read = stream.read(buffer)) != -1) {
            out.write(buffer, 0, read);
        }
        return out.toByteArray();
    }

    private String trimSlash(String value) {
        String result = value == null ? "" : value.trim();
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }

    private TextView addMessage(boolean fromUser, String text) {
        return addMessage(fromUser, text, true);
    }

    private void applyRichText(TextView view, String raw) {
        String source = raw == null ? "" : raw;
        String html = TextUtils.htmlEncode(source);
        html = html.replaceAll("(?s)```(?:[a-zA-Z0-9_+-]+)?\\n?(.*?)```", "<tt><font color='#C4FF5B'>$1</font></tt>");
        html = html.replaceAll("`([^`\\n]+)`", "<tt><font color='#C4FF5B'>$1</font></tt>");
        html = html.replaceAll("\\*\\*([^*]+)\\*\\*", "<b>$1</b>");
        html = html.replaceAll("(?m)^#{1,3}\\s+(.+)$", "<big><b>$1</b></big>");
        html = html.replace("\n", "<br>");
        view.setText(Html.fromHtml(html, Html.FROM_HTML_MODE_LEGACY));
        view.setAutoLinkMask(Linkify.WEB_URLS);
        view.setMovementMethod(LinkMovementMethod.getInstance());
        view.setTextIsSelectable(true);
        view.setOnLongClickListener(v -> {
            ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            if (clipboard != null) {
                clipboard.setPrimaryClip(ClipData.newPlainText("shushunya-message", source));
                Toast.makeText(this, "Сообщение скопировано", Toast.LENGTH_SHORT).show();
                v.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);
            }
            return true;
        });
    }

    private TextView addAgentMessage(boolean fromUser, String text, boolean animate) {
        TextView bubble = new TextView(this);
        applyRichText(bubble, text);
        bubble.setTextSize(16);
        bubble.setLineSpacing(dp(2), 1.0f);
        bubble.setTextColor(TEXT);
        bubble.setPadding(dp(16), dp(12), dp(16), dp(12));
        bubble.setBackground(fromUser
                ? gradientPill(WARP_DEEP, Color.rgb(48, 30, 92), WARP, dp(21))
                : pill(SURFACE_RAISED, LINE, dp(21)));
        bubble.setAlpha(animate ? 0f : 1f);
        bubble.setTranslationY(animate ? dp(10) : 0f);

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels - dp(58), dp(560)),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = fromUser ? Gravity.RIGHT : Gravity.LEFT;
        lp.topMargin = dp(5);
        lp.bottomMargin = dp(5);
        (agentAppendTarget != null ? agentAppendTarget : agentMessageList).addView(bubble, lp);

        if (animate) {
            bubble.animate()
                    .alpha(1f)
                    .translationY(0f)
                    .setDuration(210)
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
        }
        maybeScrollAgentToBottom(false);
        return bubble;
    }

    private void sizeImageView(ImageView imageView, Bitmap image) {
        int width = Math.min(getResources().getDisplayMetrics().widthPixels - dp(96), dp(430));
        int height = image != null
                ? Math.max(dp(160), Math.min(dp(320), Math.round(width * image.getHeight() / Math.max(1f, image.getWidth()))))
                : dp(220);
        ViewGroup.LayoutParams lp = imageView.getLayoutParams();
        if (lp == null) {
            imageView.setLayoutParams(new LinearLayout.LayoutParams(-1, height));
        } else {
            lp.height = height;
            imageView.setLayoutParams(lp);
        }
    }

    // Builds the image bubble AT its position synchronously (with a placeholder
    // if the bitmap isn't ready yet) and returns the ImageView to fill later —
    // so an async image lands in its chronological slot, never at the bottom.
    private ImageView addImageMessage(String text, Bitmap image, String fallbackLabel, int insertIndex) {
        boolean prepend = insertIndex >= 0;
        LinearLayout bubble = new LinearLayout(this);
        bubble.setOrientation(LinearLayout.VERTICAL);
        bubble.setPadding(dp(8), dp(8), dp(8), dp(8));
        bubble.setBackground(gradientPill(WARP_DEEP, Color.rgb(48, 30, 92), WARP, dp(21)));
        if (!prepend) {
            bubble.setAlpha(0f);
            bubble.setTranslationY(dp(10));
        }

        ImageView imageView = new ImageView(this);
        imageView.setScaleType(ImageView.ScaleType.CENTER_CROP);
        imageView.setBackground(pill(SURFACE, Color.argb(120, 255, 255, 255), dp(16)));
        imageView.setPadding(dp(2), dp(2), dp(2), dp(2));
        if (image != null) {
            imageView.setImageBitmap(image);
        }
        sizeImageView(imageView, image);
        bubble.addView(imageView);

        if (text != null && !text.trim().isEmpty()) {
            TextView caption = new TextView(this);
            applyRichText(caption, text.trim());
            caption.setTextSize(16);
            caption.setLineSpacing(dp(2), 1.0f);
            caption.setTextColor(TEXT);
            caption.setPadding(dp(6), dp(8), dp(6), dp(2));
            bubble.addView(caption, new LinearLayout.LayoutParams(-1, -2));
            if (!prepend) {
                saveChatMessage(true, text);
            }
        } else if (!prepend && fallbackLabel != null && !fallbackLabel.trim().isEmpty()) {
            saveChatMessage(true, fallbackLabel);
        }

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels - dp(58), dp(560)),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = Gravity.RIGHT;
        lp.topMargin = dp(6);
        lp.bottomMargin = dp(6);
        if (prepend) {
            messageList.addView(bubble, Math.min(insertIndex, messageList.getChildCount()), lp);
            return imageView;
        }
        messageList.addView(bubble, lp);

        bubble.animate()
                .alpha(1f)
                .translationY(0f)
                .setDuration(210)
                .setInterpolator(new DecelerateInterpolator())
                .start();
        return imageView;
    }

    private TextView addMessage(boolean fromUser, String text, boolean save) {
        return addMessage(fromUser, text, save, -1);
    }

    private TextView addMessage(boolean fromUser, String text, boolean save, int insertIndex) {
        boolean prepend = insertIndex >= 0;
        TextView bubble = new TextView(this);
        applyRichText(bubble, text);
        bubble.setTextSize(16);
        bubble.setLineSpacing(dp(2), 1.0f);
        bubble.setTextColor(TEXT);
        bubble.setPadding(dp(16), dp(12), dp(16), dp(12));
        bubble.setBackground(fromUser
                ? gradientPill(WARP_DEEP, Color.rgb(48, 30, 92), WARP, dp(21))
                : pill(SURFACE_RAISED, LINE, dp(21)));
        if (!prepend) {
            bubble.setAlpha(0f);
            bubble.setTranslationY(dp(10));
        }

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels - dp(58), dp(560)),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = fromUser ? Gravity.RIGHT : Gravity.LEFT;
        lp.topMargin = dp(5);
        lp.bottomMargin = dp(5);
        if (prepend) {
            messageList.addView(bubble, Math.min(insertIndex, messageList.getChildCount()), lp);
            return bubble;
        }
        messageList.addView(bubble, lp);

        bubble.animate()
                .alpha(1f)
                .translationY(0f)
                .setDuration(210)
                .setInterpolator(new DecelerateInterpolator())
                .start();
        maybeScrollToBottom(fromUser);
        if (save) {
            saveChatMessage(fromUser, text);
        }
        return bubble;
    }

    private void loadOlderChatPage() {
        if (loadingOlderChat || noOlderChat || oldestLoadedChatId <= 1 || oldestLoadedChatId == Long.MAX_VALUE) {
            return;
        }
        loadingOlderChat = true;
        final long before = oldestLoadedChatId;
        new Thread(() -> {
            JSONArray page = null;
            try {
                URL url = new URL(trimSlash(baseUrl) + "/archive/client/chat/messages?session_id=" + SERVER_CHAT_SESSION_ID
                        + "&before_id=" + before + "&limit=" + CHAT_OLDER_PAGE);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(20000);
                applyMobileAuth(conn);
                if (conn.getResponseCode() >= 200 && conn.getResponseCode() < 300) {
                    page = new JSONObject(readAll(conn.getInputStream())).optJSONArray("messages");
                }
                conn.disconnect();
            } catch (Exception ignored) {
            }
            final JSONArray finalPage = page;
            main.post(() -> {
                try {
                    if (finalPage == null || finalPage.length() == 0) {
                        noOlderChat = true;
                        return;
                    }
                    int heightBefore = messageList.getHeight();
                    int scrollBefore = scrollView.getScrollY();
                    long newOldest = oldestLoadedChatId;
                    int insertAt = 0;
                    for (int i = 0; i < finalPage.length(); i++) {
                        JSONObject item = finalPage.optJSONObject(i);
                        if (item == null) {
                            continue;
                        }
                        long id = item.optLong("id", 0);
                        if (id > 0) {
                            newOldest = Math.min(newOldest, id);
                        }
                        if (id >= before) {
                            continue;  // guard against overlap
                        }
                        String role = item.optString("role", "");
                        String text = item.optString("content", "");
                        if (messageHasAsset(item)) {
                            fetchAndRenderAsset("user".equals(role), text.trim(), item.optString("asset_id", "").trim(), insertAt);
                        } else if (!text.isEmpty()) {
                            addMessage("user".equals(role), text, false, insertAt);
                        } else {
                            continue;
                        }
                        insertAt++;
                    }
                    oldestLoadedChatId = newOldest;
                    // Keep the reading position steady as content grows above it.
                    messageList.post(() -> {
                        int delta = messageList.getHeight() - heightBefore;
                        if (delta > 0) {
                            scrollView.scrollTo(0, scrollBefore + delta);
                        }
                    });
                } finally {
                    loadingOlderChat = false;
                }
            });
        }).start();
    }

    private void loadServerChatHistory() {
        new Thread(() -> {
            try {
                URL url = new URL(trimSlash(baseUrl) + "/archive/client/chat/messages?session_id=" + SERVER_CHAT_SESSION_ID + "&limit=" + CHAT_HISTORY_LIMIT);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(12000);
                conn.setReadTimeout(30000);
                applyMobileAuth(conn);
                int code = conn.getResponseCode();
                if (code < 200 || code >= 300) {
                    return;
                }
                JSONObject payload = new JSONObject(readAll(conn.getInputStream()));
                JSONArray history = payload.optJSONArray("messages");
                if (history == null || history.length() == 0) {
                    return;
                }
                String historyKey = history.toString();
                long maxId = 0;
                long minId = Long.MAX_VALUE;
                for (int i = 0; i < history.length(); i++) {
                    JSONObject item = history.optJSONObject(i);
                    if (item != null) {
                        long id = item.optLong("id", 0);
                        maxId = Math.max(maxId, id);
                        if (id > 0) {
                            minId = Math.min(minId, id);
                        }
                    }
                }
                long finalMaxId = maxId;
                long finalMinId = minId;
                main.post(() -> {
                    lastSeenChatMessageId = Math.max(lastSeenChatMessageId, finalMaxId);
                    startChatDeltaLoop();
                    if (historyKey.equals(lastChatHistoryJson)) {
                        return;  // nothing changed: a rebuild would just blink
                    }
                    lastChatHistoryJson = historyKey;
                    oldestLoadedChatId = finalMinId;
                    noOlderChat = false;
                    messageList.removeAllViews();
                    for (int i = 0; i < history.length(); i++) {
                        JSONObject item = history.optJSONObject(i);
                        if (item == null) {
                            continue;
                        }
                        String role = item.optString("role", "");
                        String text = item.optString("content", "");
                        if (messageHasAsset(item)) {
                            fetchAndRenderAsset("user".equals(role), text.trim(), item.optString("asset_id", "").trim());
                        } else if (!text.isEmpty()) {
                            addMessage("user".equals(role), text, false);
                        }
                    }
                    maybeScrollToBottom(true);
                });
            } catch (Exception ignored) {
            }
        }).start();
    }

    private void startChatDeltaLoop() {
        if (chatDeltaLoopRunning) {
            return;
        }
        chatDeltaLoopRunning = true;
        new Thread(() -> {
            // Telegram-style delta stream: one short-lived long-poll at a time.
            // New messages are APPENDED to the view; nothing is ever rebuilt,
            // and a dropped poll is a normal outcome, not an error.
            while (true) {
                if (!appInForeground) {
                    try {
                        Thread.sleep(2000);
                    } catch (InterruptedException ignored) {
                        break;
                    }
                    continue;
                }
                try {
                    URL url = new URL(trimSlash(baseUrl) + "/archive/client/chat/messages?session_id=" + SERVER_CHAT_SESSION_ID
                            + "&after_id=" + lastSeenChatMessageId + "&wait=25&limit=50");
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(10000);
                    conn.setReadTimeout(35000);
                    applyMobileAuth(conn);
                    if (conn.getResponseCode() < 200 || conn.getResponseCode() >= 300) {
                        Thread.sleep(3000);
                        continue;
                    }
                    JSONObject payload = new JSONObject(readAll(conn.getInputStream()));
                    JSONArray delta = payload.optJSONArray("messages");
                    if (delta == null || delta.length() == 0) {
                        continue;
                    }
                    final JSONArray finalDelta = delta;
                    main.post(() -> appendChatDelta(finalDelta));
                } catch (InterruptedException stop) {
                    break;
                } catch (Exception transient_) {
                    try {
                        Thread.sleep(3000);
                    } catch (InterruptedException ignored) {
                        break;
                    }
                }
            }
        }).start();
    }

    private boolean messageHasAsset(JSONObject item) {
        String assetId = item.optString("asset_id", "").trim();
        return !assetId.isEmpty() && !"null".equals(assetId);
    }

    private void fetchAndRenderAsset(boolean fromUser, String caption, String assetId) {
        fetchAndRenderAsset(fromUser, caption, assetId, -1);
    }

    private void fetchAndRenderAsset(boolean fromUser, String caption, String assetId, int insertIndex) {
        // Place the bubble NOW, in its chronological slot, with whatever we have
        // (cached bitmap or a placeholder). The image fills in later — it never
        // jumps to the bottom.
        Bitmap cached = imageCache.get(assetId);
        final ImageView target = addImageMessage(caption, cached, caption.isEmpty() ? "[изображение]" : caption, insertIndex);
        if (insertIndex < 0) {
            maybeScrollToBottom(false);
        }
        if (cached != null) {
            return;
        }
        final String url = trimSlash(baseUrl) + "/archive/client/chat/asset/" + assetId;
        new Thread(() -> {
            Bitmap bmp = null;
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(20000);
                applyMobileAuth(conn);
                if (conn.getResponseCode() >= 200 && conn.getResponseCode() < 300) {
                    java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
                    try (java.io.InputStream in = conn.getInputStream()) {
                        byte[] chunk = new byte[8192];
                        int read;
                        while ((read = in.read(chunk)) != -1) {
                            buffer.write(chunk, 0, read);
                        }
                    }
                    byte[] raw = buffer.toByteArray();
                    // Downscale on decode: a chat bubble never needs more than ~1024px,
                    // and full-res decodes of several images are what choke the app.
                    BitmapFactory.Options bounds = new BitmapFactory.Options();
                    bounds.inJustDecodeBounds = true;
                    BitmapFactory.decodeByteArray(raw, 0, raw.length, bounds);
                    int sample = 1;
                    int longest = Math.max(bounds.outWidth, bounds.outHeight);
                    while (longest / sample > 1024) {
                        sample *= 2;
                    }
                    BitmapFactory.Options opts = new BitmapFactory.Options();
                    opts.inSampleSize = sample;
                    bmp = BitmapFactory.decodeByteArray(raw, 0, raw.length, opts);
                }
                conn.disconnect();
            } catch (Exception ignored) {
            }
            final Bitmap finalBmp = bmp;
            if (finalBmp != null) {
                imageCache.put(assetId, finalBmp);
            }
            main.post(() -> {
                if (finalBmp != null) {
                    target.setImageBitmap(finalBmp);
                    sizeImageView(target, finalBmp);
                }
            });
        }).start();
    }

    private void appendChatDelta(JSONArray delta) {
        boolean appended = false;
        for (int i = 0; i < delta.length(); i++) {
            JSONObject item = delta.optJSONObject(i);
            if (item == null) {
                continue;
            }
            long id = item.optLong("id", 0);
            if (id <= lastSeenChatMessageId) {
                continue;
            }
            lastSeenChatMessageId = Math.max(lastSeenChatMessageId, id);
            String role = item.optString("role", "");
            String text = item.optString("content", "").trim();
            boolean fromUser = "user".equals(role);
            if (messageHasAsset(item)) {
                // Image message (e.g. from Moriana): fetch the asset by id and render it.
                fetchAndRenderAsset(fromUser, text, item.optString("asset_id", "").trim());
                appended = true;
                showAnswerNotification(text.isEmpty() ? "Готово изображение" : text);
                continue;
            }
            if (text.isEmpty()) {
                continue;
            }
            String echoKey = (fromUser ? "user\n" : "assistant\n") + text;
            if (pendingLocalEchoes.remove(echoKey)) {
                continue;  // already shown locally (own message or job-delivered answer)
            }
            if (!fromUser && pendingAnswerBubble != null) {
                // The awaited answer arrives through the delta stream (e.g. after
                // a dropped poll): fill the waiting bubble instead of appending.
                applyRichText(pendingAnswerBubble, text);
                pendingAnswerBubble = null;
                setWaiting(false);
                showAnswerNotification(text);
                appended = true;
                continue;
            }
            addMessage(fromUser, text, false);
            appended = true;
        }
        if (appended) {
            lastChatHistoryJson = "";  // view diverged from last full snapshot
            maybeScrollToBottom(false);
        }
    }

    private void pollPendingReports() {
        new Thread(() -> {
            int count = 0;
            try {
                // Badge: read-only summary, no marking.
                URL url = new URL(trimSlash(baseUrl) + "/archive/client/chat/reports/pending");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(8000);
                conn.setReadTimeout(12000);
                applyMobileAuth(conn);
                if (conn.getResponseCode() >= 200 && conn.getResponseCode() < 300) {
                    JSONObject payload = new JSONObject(readAll(conn.getInputStream()));
                    count = payload.optInt("count", 0);
                }
                // Buzzing is owned by VoxNotifyService (survives backgrounding);
                // this in-app poll only keeps the badge fresh.
            } catch (Exception ignored) {
            }
            int finalCount = count;
            main.post(() -> {
                updateReportsBadge(finalCount);
                if (!reportsPollScheduled) {
                    reportsPollScheduled = true;
                    main.postDelayed(() -> {
                        reportsPollScheduled = false;
                        pollPendingReports();
                    }, appInForeground ? 30000 : 120000);
                }
            });
        }).start();
    }

    private void showVoxNotification(int count, java.util.List<String> lines) {
        if (lines.isEmpty()) {
            return;
        }
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(this, 8, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        String body = TextUtils.join("\n", lines);
        body = TextUtils.ellipsize(body, new android.text.TextPaint(), 600, TextUtils.TruncateAt.END).toString();
        android.app.Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new android.app.Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
                : new android.app.Notification.Builder(this);
        builder.setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentTitle("Шушуня хочет что-то сказать")
                .setContentText(body)
                .setStyle(new android.app.Notification.BigTextStyle().bigText(body))
                .setContentIntent(pendingIntent)
                .setAutoCancel(true);
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.notify(1002, builder.build());
        }
    }

    private void updateReportsBadge(int count) {
        if (reportsButton == null) {
            return;
        }
        if (count > 0) {
            reportsButton.setText("✉ " + count);
            reportsButton.setVisibility(View.VISIBLE);
        } else {
            reportsButton.setVisibility(View.GONE);
        }
    }

    private void deliverPendingReports() {
        if (reportsButton != null) {
            reportsButton.setEnabled(false);
            reportsButton.setText("✉ …");
        }
        new Thread(() -> {
            try {
                URL url = new URL(trimSlash(baseUrl) + "/archive/client/chat/reports/deliver");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(8000);
                conn.setReadTimeout(15000);
                conn.setDoOutput(true);
                applyMobileAuth(conn);
                conn.getOutputStream().write("{}".getBytes());
                readAll(conn.getInputStream());
            } catch (Exception ignored) {
            }
            main.post(() -> {
                if (reportsButton != null) {
                    reportsButton.setEnabled(true);
                    reportsButton.setVisibility(View.GONE);
                }
                // The voiced reports arrive through the chat delta stream.
            });
        }).start();
    }

    private void saveChatMessage(boolean fromUser, String text) {
        // Server-side chat history is the source of truth.
    }

    private boolean isAtChatBottom() {
        int range = Math.max(0, messageList.getHeight() + scrollView.getPaddingBottom() - scrollView.getHeight());
        return range - scrollView.getScrollY() <= dp(24);
    }

    private boolean isAtAgentBottom() {
        if (agentMessageList == null || agentScrollView == null) {
            return true;
        }
        int range = Math.max(0, agentMessageList.getHeight() + agentScrollView.getPaddingBottom() - agentScrollView.getHeight());
        return range - agentScrollView.getScrollY() <= dp(24);
    }

    private void maybeScrollToBottom(boolean force) {
        if (!force && (chatTouchActive || userPinnedScroll || !chatAutoFollow)) {
            return;
        }
        if (force) {
            userPinnedScroll = false;
            chatAutoFollow = true;
        }
        main.postDelayed(() -> {
            // The user may have touched or moved the history after this callback
            // was queued by a previous token. Re-check here, not only above.
            if (chatTouchActive || userPinnedScroll || !chatAutoFollow) {
                return;
            }
            int target = Math.max(0, messageList.getBottom() + scrollView.getPaddingBottom() - scrollView.getHeight());
            if (scrollAnimator != null) {
                scrollAnimator.cancel();
                scrollAnimator = null;
            }
            if (!force) {
                scrollView.scrollTo(0, target);
                return;
            }
            scrollAnimator = ValueAnimator.ofInt(scrollView.getScrollY(), target);
            scrollAnimator.setDuration(150);
            scrollAnimator.setInterpolator(new DecelerateInterpolator());
            scrollAnimator.addUpdateListener(a -> scrollView.scrollTo(0, (int) a.getAnimatedValue()));
            scrollAnimator.start();
        }, 60);
    }

    private void maybeScrollAgentToBottom(boolean force) {
        if (agentScrollView == null || agentMessageList == null) {
            return;
        }
        if (!force && (agentTouchActive || agentPinnedScroll)) {
            return;
        }
        if (force) {
            agentPinnedScroll = false;
        }
        main.postDelayed(() -> {
            int target = Math.max(0, agentMessageList.getBottom() + agentScrollView.getPaddingBottom() - agentScrollView.getHeight());
            if (agentScrollAnimator != null) {
                agentScrollAnimator.cancel();
                agentScrollAnimator = null;
            }
            if (!force) {
                agentScrollView.scrollTo(0, target);
                return;
            }
            agentScrollAnimator = ValueAnimator.ofInt(agentScrollView.getScrollY(), target);
            agentScrollAnimator.setDuration(150);
            agentScrollAnimator.setInterpolator(new DecelerateInterpolator());
            agentScrollAnimator.addUpdateListener(a -> agentScrollView.scrollTo(0, (int) a.getAnimatedValue()));
            agentScrollAnimator.start();
        }, 60);
    }

    private void setWaiting(boolean value) {
        waiting = value;
        send.setEnabled(!value);
        send.animate().alpha(value ? 0.55f : 1f).setDuration(180).start();
        progress.setVisibility(value ? View.VISIBLE : View.GONE);
        if (TAB_CHAT.equals(currentTab)) {
            setWarpState(value ? "●  ШУШУНЯ ПЛЕТЁТ ОТВЕТ" : "●  ВАРП-КАНАЛ / В СЕТИ", value ? WARP : CYAN);
        }
    }

    @Override
    protected void onDestroy() {
        recording = false;
        if (warpAnimator != null) {
            warpAnimator.cancel();
            warpAnimator = null;
        }
        super.onDestroy();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private class ImageGridAdapter extends BaseAdapter {
        private final ArrayList<Uri> images;

        ImageGridAdapter(ArrayList<Uri> images) {
            this.images = images;
        }

        @Override
        public int getCount() {
            return images.size();
        }

        @Override
        public Object getItem(int position) {
            return images.get(position);
        }

        @Override
        public long getItemId(int position) {
            return position;
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            ImageView image = convertView instanceof ImageView ? (ImageView) convertView : new ImageView(MainActivity.this);
            image.setLayoutParams(new GridView.LayoutParams(-1, dp(112)));
            image.setScaleType(ImageView.ScaleType.CENTER_CROP);
            image.setBackground(pill(SURFACE_RAISED, LINE, dp(12)));
            image.setPadding(dp(2), dp(2), dp(2), dp(2));
            try {
                Bitmap thumb = getContentResolver().loadThumbnail(images.get(position), new Size(dp(160), dp(160)), null);
                image.setImageBitmap(thumb);
            } catch (Exception ignored) {
                image.setImageResource(android.R.drawable.ic_menu_gallery);
                image.setColorFilter(TEXT_MUTED);
            }
            return image;
        }
    }

    private class StreamingBubble {
        private final TextView bubble;
        private final StringBuilder target = new StringBuilder();
        private int shown;
        private boolean finished;
        private boolean ticking;

        StreamingBubble(TextView bubble) {
            this.bubble = bubble;
        }

        void start() {
            main.post(() -> {
                bubble.setText("▌");
                tick();
            });
        }

        void append(String delta) {
            synchronized (target) {
                target.append(delta);
            }
            main.post(this::tick);
        }

        void finish() {
            finished = true;
            main.post(this::tick);
        }

        String targetText() {
            synchronized (target) {
                return target.toString().trim();
            }
        }

        private void tick() {
            if (ticking) {
                return;
            }
            ticking = true;
            main.postDelayed(() -> {
                int available;
                synchronized (target) {
                    available = target.length();
                    if (shown < available) {
                        int remaining = available - shown;
                        shown += Math.min(Math.max(1, remaining / 5), 18);
                    }
                    String visible = target.substring(0, shown);
                    if (visible.trim().isEmpty() && finished) {
                        bubble.setText("Пусто. Даже варп иногда молчит.");
                    } else if (finished && shown >= available) {
                        applyRichText(bubble, visible.trim());
                        restoreHeaderState();
                        bubble.performHapticFeedback(HapticFeedbackConstants.CONFIRM);
                    } else {
                        bubble.setText(visible + "▌");
                    }
                }
                maybeScrollToBottom(false);
                ticking = false;
                synchronized (target) {
                    if (shown < target.length()) {
                        tick();
                    }
                }
            }, 28);
        }
    }
}
