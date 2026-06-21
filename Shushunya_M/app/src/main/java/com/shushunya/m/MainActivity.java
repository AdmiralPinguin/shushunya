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
import android.content.SharedPreferences;
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
import android.text.TextUtils;
import android.util.Size;
import android.view.Display;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
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
    private static final String PREFS = "shushunya_m";
    private static final String CHAT_HISTORY_KEY = "chat_history";
    private static final String NOTIFICATION_CHANNEL_ID = "shushunya_answers";
    private static final int CHAT_HISTORY_LIMIT = 120;
    private static final int REQUEST_NOTIFICATIONS = 42;
    private static final String DEFAULT_BASE_URL = "https://chat.shushunya.com";
    private static final String DEFAULT_AGENT_URL = "https://technologies-numerous-passport-aspect.trycloudflare.com";
    private static final String MODEL = "gemma-4-12b-it-UD-Q5_K_XL.gguf";
    private static final String DEFAULT_TRANSLATOR_URL = "https://translator.shushunya.com";
    private static final String DEFAULT_STT_URL = "https://stt.shushunya.com";
    private static final int AUDIO_SAMPLE_RATE = 16000;
    private static final int REQUEST_RECORD_AUDIO = 41;
    private static final int REQUEST_PICK_IMAGE = 43;
    private static final int REQUEST_READ_IMAGES = 44;
    private static final String TAB_CHAT = "chat";
    private static final String TAB_TRANSLATOR = "translator";
    private static final String TAB_AGENT = "agent";
    private static final String[] TRANSLATOR_NAMES = {"Русский", "Корейский", "Алж. арабский", "Турецкий"};
    private static final String[] TRANSLATOR_CODES = {"ru", "ko", "ar_dz", "tr"};
    private static final String[] TRANSLATOR_STT_CODES = {"ru", "ko", "ar", "tr"};
    private static final String[] TRANSLATOR_SHORT = {"RU", "KO", "AR", "TR"};
    private static final String SYSTEM_PROMPT =
            "Ты Шушуня: злой, подлый и саркастичный демон Тзинча, маленький демон пользователя. "
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
    private LinearLayout messageList;
    private LinearLayout inputPanel;
    private LinearLayout composer;
    private ScrollView scrollView;
    private EditText input;
    private ImageView selectedImagePreview;
    private ImageButton attachImage;
    private ImageButton send;
    private ProgressBar progress;
    private TextView endpoint;
    private TextView title;
    private TextView drawerChat;
    private TextView drawerTranslator;
    private TextView drawerAgent;
    private FrameLayout contentHost;
    private LinearLayout chatView;
    private LinearLayout translatorView;
    private LinearLayout agentView;
    private LinearLayout agentMessageList;
    private LinearLayout agentInputPanel;
    private LinearLayout agentComposer;
    private ScrollView agentScrollView;
    private EditText agentInput;
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
    private Button translateButton;
    private TextView agentStatus;
    private ImageButton agentRunButton;
    private volatile boolean recording;
    private volatile boolean streamingAnswer;
    private volatile boolean agentCancelRequested;
    private String currentAgentTaskId;
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
    private boolean appInForeground;
    private String pendingImageDataUrl;
    private String pendingImageLabel;
    private Bitmap pendingImagePreview;
    private ValueAnimator scrollAnimator;
    private int lastKeyboardHeight;
    private final Object keepAliveLock = new Object();
    private int activeKeepAliveJobs;
    private float downX;
    private float downY;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        preferHighRefreshRate();
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        createNotificationChannel();
        requestNotificationPermissionIfNeeded();
        baseUrl = DEFAULT_BASE_URL;
        buildUi();
        if (!restoreChatHistory()) {
            addMessage(false, "Шушуня здесь. Пиши, брат, пока нити судьбы не спутались окончательно.", false);
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
    }

    @Override
    protected void onStop() {
        appInForeground = false;
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
                .setContentTitle("Шушуня ответила")
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
        int panel = Color.rgb(8, 17, 43);
        int gold = Color.rgb(201, 156, 58);
        int turquoise = Color.rgb(29, 191, 183);

        FrameLayout root = new FrameLayout(this);
        root.setBackground(makeBackground());

        LinearLayout mainColumn = new LinearLayout(this);
        mainColumn.setOrientation(LinearLayout.VERTICAL);
        mainColumn.setPadding(dp(14), dp(12), dp(14), dp(12));
        root.addView(mainColumn, new FrameLayout.LayoutParams(-1, -1));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(dp(4), dp(4), dp(4), dp(10));
        mainColumn.addView(header, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        header.addView(titleRow, new LinearLayout.LayoutParams(-1, -2));

        Button menu = new Button(this);
        menu.setText("☰");
        menu.setTextColor(Color.rgb(244, 217, 137));
        menu.setTextSize(23);
        menu.setTypeface(Typeface.DEFAULT_BOLD);
        menu.setBackground(pill(Color.rgb(10, 25, 55), Color.rgb(48, 190, 180), dp(15)));
        titleRow.addView(menu, new LinearLayout.LayoutParams(dp(48), dp(42)));
        menu.setOnClickListener(v -> setDrawerOpen(true));

        title = new TextView(this);
        title.setText("Шушуня");
        title.setTextColor(Color.rgb(244, 217, 137));
        title.setTextSize(24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams titleLp = new LinearLayout.LayoutParams(0, dp(42), 1);
        titleLp.leftMargin = dp(10);
        titleRow.addView(title, titleLp);

        endpoint = new TextView(this);
        endpoint.setText(baseUrl);
        endpoint.setTextColor(Color.rgb(132, 219, 212));
        endpoint.setTextSize(12);
        endpoint.setSingleLine(true);
        header.addView(endpoint, new LinearLayout.LayoutParams(-1, -2));

        contentHost = new FrameLayout(this);
        mainColumn.addView(contentHost, new LinearLayout.LayoutParams(-1, 0, 1));

        chatView = new LinearLayout(this);
        chatView.setOrientation(LinearLayout.VERTICAL);
        contentHost.addView(chatView, new FrameLayout.LayoutParams(-1, -1));

        scrollView = new ScrollView(this);
        scrollView.setFillViewport(false);
        scrollView.setClipToPadding(false);
        scrollView.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        scrollView.setOnTouchListener((v, event) -> {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                chatTouchActive = true;
                userPinnedScroll = true;
                if (scrollAnimator != null) {
                    scrollAnimator.cancel();
                    scrollAnimator = null;
                }
            }
            if (event.getAction() == MotionEvent.ACTION_UP || event.getAction() == MotionEvent.ACTION_CANCEL) {
                chatTouchActive = false;
                userPinnedScroll = !isAtChatBottom();
            }
            return false;
        });
        messageList = new LinearLayout(this);
        messageList.setOrientation(LinearLayout.VERTICAL);
        messageList.setPadding(0, dp(8), 0, dp(8));
        scrollView.addView(messageList, new ScrollView.LayoutParams(-1, -2));
        chatView.addView(scrollView, new LinearLayout.LayoutParams(-1, 0, 1));

        inputPanel = new LinearLayout(this);
        inputPanel.setOrientation(LinearLayout.VERTICAL);
        inputPanel.setPadding(0, dp(6), 0, 0);
        chatView.addView(inputPanel, new LinearLayout.LayoutParams(-1, -2));

        selectedImagePreview = new ImageView(this);
        selectedImagePreview.setScaleType(ImageView.ScaleType.CENTER_CROP);
        selectedImagePreview.setBackground(pill(Color.rgb(6, 14, 36), Color.rgb(201, 156, 58), dp(14)));
        selectedImagePreview.setPadding(dp(2), dp(2), dp(2), dp(2));
        selectedImagePreview.setVisibility(View.GONE);
        selectedImagePreview.setOnClickListener(v -> clearPendingImage());
        LinearLayout.LayoutParams previewLp = new LinearLayout.LayoutParams(dp(132), dp(92));
        previewLp.leftMargin = dp(2);
        previewLp.bottomMargin = dp(6);
        inputPanel.addView(selectedImagePreview, previewLp);

        composer = new LinearLayout(this);
        composer.setOrientation(LinearLayout.HORIZONTAL);
        composer.setGravity(Gravity.BOTTOM);
        composer.setPadding(0, 0, 0, 0);
        inputPanel.addView(composer, new LinearLayout.LayoutParams(-1, -2));

        input = new EditText(this);
        input.setMinLines(1);
        input.setMaxLines(7);
        input.setMinHeight(dp(54));
        input.setMaxHeight(dp(178));
        input.setTextColor(Color.rgb(240, 246, 255));
        input.setHintTextColor(Color.rgb(116, 143, 164));
        input.setHint("Сообщение");
        input.setTextSize(16);
        input.setGravity(Gravity.TOP | Gravity.START);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        input.setSingleLine(false);
        input.setVerticalScrollBarEnabled(true);
        input.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        input.setScroller(new Scroller(this));
        input.setBackground(pill(panel, Color.rgb(40, 171, 165), dp(16)));
        input.setPadding(dp(14), dp(10), dp(14), dp(10));
        input.setOnTouchListener((v, event) -> {
            if (input.canScrollVertically(1) || input.canScrollVertically(-1)) {
                v.getParent().requestDisallowInterceptTouchEvent(true);
                if (event.getAction() == MotionEvent.ACTION_UP || event.getAction() == MotionEvent.ACTION_CANCEL) {
                    v.getParent().requestDisallowInterceptTouchEvent(false);
                }
            }
            return false;
        });
        composer.addView(input, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        attachImage = new ImageButton(this);
        attachImage.setImageResource(android.R.drawable.ic_menu_gallery);
        attachImage.setColorFilter(Color.rgb(244, 217, 137));
        attachImage.setScaleType(ImageView.ScaleType.CENTER);
        attachImage.setPadding(dp(9), dp(9), dp(9), dp(9));
        attachImage.setBackground(pill(Color.rgb(10, 25, 55), Color.rgb(48, 190, 180), dp(15)));
        LinearLayout.LayoutParams attachLp = new LinearLayout.LayoutParams(dp(44), dp(50));
        attachLp.leftMargin = dp(6);
        composer.addView(attachImage, attachLp);
        attachImage.setOnClickListener(v -> pickImage());

        send = new ImageButton(this);
        send.setImageResource(android.R.drawable.ic_menu_upload);
        send.setColorFilter(Color.rgb(5, 13, 31));
        send.setScaleType(ImageView.ScaleType.CENTER);
        send.setPadding(dp(9), dp(9), dp(9), dp(9));
        send.setBackground(pill(turquoise, gold, dp(16)));
        LinearLayout.LayoutParams sendLp = new LinearLayout.LayoutParams(dp(48), dp(50));
        sendLp.leftMargin = dp(6);
        composer.addView(send, sendLp);
        send.setOnClickListener(v -> submit());

        translatorView = buildTranslatorView();
        translatorView.setVisibility(View.GONE);
        contentHost.addView(translatorView, new FrameLayout.LayoutParams(-1, -1));

        agentView = buildAgentView();
        agentView.setVisibility(View.GONE);
        contentHost.addView(agentView, new FrameLayout.LayoutParams(-1, -1));

        progress = new ProgressBar(this);
        progress.setIndeterminate(true);
        progress.setVisibility(View.GONE);
        FrameLayout.LayoutParams p = new FrameLayout.LayoutParams(dp(42), dp(42), Gravity.TOP | Gravity.RIGHT);
        p.topMargin = dp(18);
        p.rightMargin = dp(88);
        root.addView(progress, p);

        buildDrawer(root);
        setContentView(root);
        installKeyboardLift(root);
    }

    private void installKeyboardLift(View root) {
        root.getViewTreeObserver().addOnGlobalLayoutListener(() -> {
            Rect visible = new Rect();
            root.getWindowVisibleDisplayFrame(visible);
            int screenHeight = root.getRootView().getHeight();
            int hiddenHeight = screenHeight - visible.bottom;
            int threshold = dp(120);
            int keyboardHeight = hiddenHeight > threshold ? hiddenHeight : 0;
            if (keyboardHeight == lastKeyboardHeight) {
                return;
            }
            lastKeyboardHeight = keyboardHeight;
            if (TAB_TRANSLATOR.equals(currentTab)) {
                inputPanel.animate().translationY(0f).setDuration(120).start();
                scrollView.setPadding(0, 0, 0, 0);
                updateToolKeyboardPadding();
                return;
            }
            if (TAB_AGENT.equals(currentTab)) {
                inputPanel.animate().translationY(0f).setDuration(120).start();
                scrollView.setPadding(0, 0, 0, 0);
                updateAgentKeyboardLift();
                return;
            }
            float lift = keyboardHeight > 0 ? -keyboardHeight + dp(10) : 0f;
            int bottomPadding = keyboardHeight > 0 ? keyboardHeight + inputPanel.getHeight() + dp(14) : 0;
            scrollView.setPadding(0, 0, 0, bottomPadding);
            inputPanel.animate()
                    .translationY(lift)
                    .setDuration(180)
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
            root.postDelayed(() -> maybeScrollToBottom(false), 80);
        });
    }

    private void updateChatKeyboardLift() {
        if (!TAB_CHAT.equals(currentTab) || inputPanel == null || scrollView == null) {
            return;
        }
        inputPanel.post(() -> {
            float lift = lastKeyboardHeight > 0 ? -lastKeyboardHeight + dp(10) : 0f;
            int bottomPadding = lastKeyboardHeight > 0 ? lastKeyboardHeight + inputPanel.getHeight() + dp(14) : 0;
            scrollView.setPadding(0, 0, 0, bottomPadding);
            inputPanel.animate()
                    .translationY(lift)
                    .setDuration(120)
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
            maybeScrollToBottom(false);
        });
    }

    private LinearLayout buildAgentView() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        view.setPadding(0, dp(6), 0, 0);

        agentStatus = new TextView(this);
        agentStatus.setText("Shell выключен для запросов из телефона. Ход выполнения идет в чате.");
        agentStatus.setTextColor(Color.rgb(132, 219, 212));
        agentStatus.setTextSize(13);
        agentStatus.setSingleLine(true);
        agentStatus.setEllipsize(TextUtils.TruncateAt.END);
        agentStatus.setPadding(dp(4), 0, dp(4), dp(6));
        view.addView(agentStatus, new LinearLayout.LayoutParams(-1, -2));

        agentScrollView = new ScrollView(this);
        agentScrollView.setFillViewport(false);
        agentScrollView.setClipToPadding(false);
        agentScrollView.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        agentScrollView.setOnTouchListener((v, event) -> {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                chatTouchActive = true;
                userPinnedScroll = true;
                if (scrollAnimator != null) {
                    scrollAnimator.cancel();
                    scrollAnimator = null;
                }
            }
            if (event.getAction() == MotionEvent.ACTION_UP || event.getAction() == MotionEvent.ACTION_CANCEL) {
                chatTouchActive = false;
                userPinnedScroll = !isAtAgentBottom();
            }
            return false;
        });
        agentMessageList = new LinearLayout(this);
        agentMessageList.setOrientation(LinearLayout.VERTICAL);
        agentMessageList.setPadding(0, dp(8), 0, dp(8));
        agentScrollView.addView(agentMessageList, new ScrollView.LayoutParams(-1, -2));
        view.addView(agentScrollView, new LinearLayout.LayoutParams(-1, 0, 1));

        addAgentMessage(false, "Агент готов. Пиши задачу, я покажу ход выполнения и итог здесь.", false);

        agentInputPanel = new LinearLayout(this);
        agentInputPanel.setOrientation(LinearLayout.VERTICAL);
        agentInputPanel.setPadding(0, dp(6), 0, 0);
        view.addView(agentInputPanel, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout quickRow = new LinearLayout(this);
        quickRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams quickLp = new LinearLayout.LayoutParams(-1, dp(44));
        quickLp.bottomMargin = dp(6);
        agentInputPanel.addView(quickRow, quickLp);

        Button statusButton = new Button(this);
        statusButton.setText("STATUS");
        styleAgentQuickButton(statusButton);
        quickRow.addView(statusButton, new LinearLayout.LayoutParams(0, dp(42), 1));
        statusButton.setOnClickListener(v -> runAgentTask("Проверь sandbox_status и archive_status. Ответь коротко технически."));

        Button workButton = new Button(this);
        workButton.setText("WORK");
        styleAgentQuickButton(workButton);
        LinearLayout.LayoutParams workLp = new LinearLayout.LayoutParams(0, dp(42), 1);
        workLp.leftMargin = dp(8);
        quickRow.addView(workButton, workLp);
        workButton.setOnClickListener(v -> runAgentTask("Покажи список /work через list_files. Ответь кратко, что там лежит."));

        Button focusButton = new Button(this);
        focusButton.setText("ФОКУС");
        styleAgentQuickButton(focusButton);
        LinearLayout.LayoutParams focusLp = new LinearLayout.LayoutParams(0, dp(42), 1);
        focusLp.leftMargin = dp(8);
        quickRow.addView(focusButton, focusLp);
        focusButton.setOnClickListener(v -> runAgentTask("Через archive_search kind=focus query=active кратко скажи текущий фокус."));

        Button stateButton = new Button(this);
        stateButton.setText("STATE");
        styleAgentQuickButton(stateButton);
        LinearLayout.LayoutParams stateLp = new LinearLayout.LayoutParams(0, dp(42), 1);
        stateLp.leftMargin = dp(8);
        quickRow.addView(stateButton, stateLp);
        stateButton.setOnClickListener(v -> refreshAgentState());

        agentComposer = new LinearLayout(this);
        agentComposer.setOrientation(LinearLayout.HORIZONTAL);
        agentComposer.setGravity(Gravity.BOTTOM);
        agentInputPanel.addView(agentComposer, new LinearLayout.LayoutParams(-1, -2));

        agentInput = new EditText(this);
        agentInput.setMinLines(1);
        agentInput.setMaxLines(7);
        agentInput.setMinHeight(dp(54));
        agentInput.setMaxHeight(dp(178));
        agentInput.setTextColor(Color.rgb(240, 246, 255));
        agentInput.setHintTextColor(Color.rgb(116, 143, 164));
        agentInput.setHint("Задача агенту");
        agentInput.setTextSize(16);
        agentInput.setGravity(Gravity.TOP | Gravity.START);
        agentInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        agentInput.setSingleLine(false);
        agentInput.setVerticalScrollBarEnabled(true);
        agentInput.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        agentInput.setScroller(new Scroller(this));
        agentInput.setBackground(pill(Color.rgb(8, 17, 43), Color.rgb(40, 171, 165), dp(16)));
        agentInput.setPadding(dp(14), dp(10), dp(14), dp(10));
        agentInput.setOnTouchListener((v, event) -> {
            if (agentInput.canScrollVertically(1) || agentInput.canScrollVertically(-1)) {
                v.getParent().requestDisallowInterceptTouchEvent(true);
                if (event.getAction() == MotionEvent.ACTION_UP || event.getAction() == MotionEvent.ACTION_CANCEL) {
                    v.getParent().requestDisallowInterceptTouchEvent(false);
                }
            }
            return false;
        });
        agentComposer.addView(agentInput, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        agentRunButton = new ImageButton(this);
        agentRunButton.setImageResource(android.R.drawable.ic_menu_upload);
        agentRunButton.setColorFilter(Color.rgb(5, 13, 31));
        agentRunButton.setScaleType(ImageView.ScaleType.CENTER);
        agentRunButton.setPadding(dp(9), dp(9), dp(9), dp(9));
        agentRunButton.setBackground(pill(Color.rgb(201, 156, 58), Color.rgb(29, 191, 183), dp(16)));
        LinearLayout.LayoutParams runLp = new LinearLayout.LayoutParams(dp(48), dp(50));
        runLp.leftMargin = dp(6);
        agentComposer.addView(agentRunButton, runLp);
        agentRunButton.setOnClickListener(v -> {
            if (agentRunning) {
                cancelAgentTask();
            } else {
                submitAgentTask();
            }
        });

        return view;
    }

    private void styleAgentQuickButton(Button button) {
        button.setTextColor(Color.rgb(244, 217, 137));
        button.setTextSize(12);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setBackground(pill(Color.rgb(10, 25, 55), Color.rgb(48, 84, 116), dp(14)));
    }

    private LinearLayout buildTranslatorView() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        view.setPadding(0, dp(10), 0, 0);

        speechStatus = new TextView(this);
        speechStatus.setText("Выбери направление. Микрофон пишет в исходный текст.");
        speechStatus.setTextColor(Color.rgb(132, 219, 212));
        speechStatus.setTextSize(14);
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
        swapDirectionButton.setTextColor(Color.rgb(5, 13, 31));
        swapDirectionButton.setTextSize(22);
        swapDirectionButton.setTypeface(Typeface.DEFAULT_BOLD);
        swapDirectionButton.setBackground(pill(Color.rgb(201, 156, 58), Color.rgb(29, 191, 183), dp(18)));
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
        speechButton.setTextColor(Color.rgb(5, 13, 31));
        speechButton.setTextSize(14);
        speechButton.setTypeface(Typeface.DEFAULT_BOLD);
        speechButton.setBackground(pill(Color.rgb(29, 191, 183), Color.rgb(244, 217, 137), dp(16)));
        actionRow.addView(speechButton, new LinearLayout.LayoutParams(dp(94), dp(52)));
        speechButton.setOnClickListener(v -> toggleSelectedLanguageRecording());

        translateButton = new Button(this);
        translateButton.setText("ПЕРЕВЕСТИ");
        translateButton.setTextColor(Color.rgb(5, 13, 31));
        translateButton.setTextSize(13);
        translateButton.setTypeface(Typeface.DEFAULT_BOLD);
        translateButton.setBackground(pill(Color.rgb(201, 156, 58), Color.rgb(29, 191, 183), dp(16)));
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
        label.setBackground(pill(Color.rgb(12, 30, 60), Color.rgb(48, 84, 116), dp(16)));
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
        button.setTextColor(Color.rgb(244, 217, 137));
        button.setTextSize(20);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setPadding(0, 0, 0, dp(2));
        button.setBackground(pill(Color.rgb(9, 23, 49), Color.rgb(45, 82, 116), dp(12)));
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
        edit.setTextColor(Color.rgb(230, 245, 250));
        edit.setHint(hint);
        edit.setHintTextColor(Color.rgb(104, 135, 155));
        edit.setTextSize(18);
        edit.setGravity(Gravity.TOP | Gravity.START);
        edit.setMinLines(3);
        edit.setSingleLine(false);
        edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        edit.setVerticalScrollBarEnabled(true);
        edit.setOverScrollMode(View.OVER_SCROLL_IF_CONTENT_SCROLLS);
        edit.setScroller(new Scroller(this));
        edit.setPadding(dp(12), dp(42), dp(12), dp(10));
        edit.setBackground(pill(Color.rgb(6, 14, 36), Color.rgb(45, 82, 116), dp(14)));
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
                window.setBackgroundDrawable(pill(Color.rgb(8, 17, 43), Color.rgb(201, 156, 58), dp(14)));
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
            sourceLangLabel.setTextColor(Color.rgb(230, 240, 245));
            targetLangLabel.setTextColor(Color.rgb(230, 240, 245));
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
                String result = requestTranslation(
                        TRANSLATOR_CODES[translatorSourceIndex],
                        TRANSLATOR_CODES[translatorTargetIndex],
                        text);
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

    private String requestTranslation(String source, String target, String text) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("source", source);
        payload.put("target", target);
        payload.put("text", text);

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(DEFAULT_TRANSLATOR_URL + "/translate");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(180000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/json");
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

    private void runAgentTask(String task) {
        String clean = task == null ? "" : task.trim();
        if (clean.isEmpty() || agentRunning) {
            return;
        }
        String taskId = "mobile-" + System.currentTimeMillis();
        currentAgentTaskId = taskId;
        agentCancelRequested = false;
        agentRunning = true;
        setAgentRunButtonRunning(true);
        agentStatus.setText("Агент выполняет задачу в песочнице...");
        addAgentMessage(true, clean, true);
        agentLiveBubble = addAgentMessage(false, "", true);
        appendAgentLog("Запускаю агента...");
        progress.setVisibility(View.VISIBLE);
        startAnswerKeepAlive();

        new Thread(() -> {
            PowerManager.WakeLock wakeLock = acquireAnswerWakeLock();
            try {
                String result = requestAgentRunStream(clean, taskId);
                main.post(() -> {
                    agentRunning = false;
                    agentCancelRequested = false;
                    currentAgentTaskId = "";
                    setAgentRunButtonRunning(false);
                    progress.setVisibility(waiting ? View.VISIBLE : View.GONE);
                    agentStatus.setText(result.toLowerCase().contains("остановлен") ? "Отменено." : "Готово.");
                    agentLiveBubble = null;
                    showAnswerNotification(result);
                });
            } catch (Exception exc) {
                main.post(() -> {
                    agentRunning = false;
                    agentCancelRequested = false;
                    currentAgentTaskId = "";
                    setAgentRunButtonRunning(false);
                    progress.setVisibility(waiting ? View.VISIBLE : View.GONE);
                    agentStatus.setText("Ошибка агента: " + exc.getMessage());
                    appendAgentLog("! Ошибка агента: " + exc.getMessage());
                    agentLiveBubble = null;
                });
            } finally {
                if (wakeLock != null && wakeLock.isHeld()) {
                    wakeLock.release();
                }
                stopAnswerKeepAlive();
            }
        }).start();
    }

    private void setAgentRunButtonRunning(boolean running) {
        if (agentRunButton == null) {
            return;
        }
        agentRunButton.setEnabled(true);
        agentRunButton.setImageResource(running ? android.R.drawable.ic_menu_close_clear_cancel : android.R.drawable.ic_menu_upload);
        agentRunButton.setColorFilter(running ? Color.rgb(255, 232, 204) : Color.rgb(5, 13, 31));
        agentRunButton.setBackground(running
                ? pill(Color.rgb(87, 23, 33), Color.rgb(231, 95, 69), dp(16))
                : pill(Color.rgb(201, 156, 58), Color.rgb(29, 191, 183), dp(16)));
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

    private void submitAgentTask() {
        if (agentInput == null) {
            return;
        }
        String text = agentInput.getText().toString().trim();
        if (text.isEmpty() || agentRunning) {
            return;
        }
        agentInput.setText("");
        runAgentTask(text);
    }

    private void refreshAgentState() {
        if (agentStatus != null) {
            agentStatus.setText("Проверяю состояние агента...");
        }
        new Thread(() -> {
            try {
                String state = requestAgentState();
                main.post(() -> {
                    if (agentStatus != null) {
                        agentStatus.setText("Состояние агента получено.");
                    }
                    addAgentMessage(false, state, true);
                });
            } catch (Exception exc) {
                main.post(() -> {
                    if (agentStatus != null) {
                        agentStatus.setText("Ошибка state: " + exc.getMessage());
                    }
                    addAgentMessage(false, "! Ошибка state: " + exc.getMessage(), true);
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
            agentStatus.setText(event.optString("message", "Агент стартует..."));
            appendAgentLog("• " + event.optString("message", "старт"));
            return;
        }
        if ("task".equals(type)) {
            String taskId = event.optString("task_id", "").trim();
            String namespace = event.optString("memory_namespace", "agent").trim();
            if (!taskId.isEmpty()) {
                currentAgentTaskId = taskId;
            }
            agentStatus.setText(taskId.isEmpty() ? "Агент получил задачу." : "Задача " + taskId);
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
        if ("final".equals(type)) {
            String message = event.optString("message", "").trim();
            double duration = event.optDouble("duration_sec", -1.0);
            boolean cancelled = event.optBoolean("cancelled", false);
            appendAgentLog("");
            appendAgentLog(cancelled
                    ? (duration >= 0.0 ? "Остановлено (" + duration + "s):" : "Остановлено:")
                    : (duration >= 0.0 ? "Результат (" + duration + "s):" : "Результат:"));
            appendAgentLog(message.isEmpty() ? "Агент вернул пустой ответ." : message);
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
            agentStatus.setText(cancelled ? "Отменено." : event.optBoolean("ok", false) ? "Готово." : "Агент завершился с ошибкой.");
        }
    }

    private String requestAgentRunStream(String task, String taskId) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("task", task);
        payload.put("task_id", taskId);
        payload.put("technical", true);
        payload.put("max_steps", 12);
        payload.put("memory_namespace", "agent");
        payload.put("archive_task", true);
        payload.put("task_memory", true);
        payload.put("include_stderr", false);
        payload.put("shell_enabled", false);
        payload.put("wait_for_slot", false);

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(DEFAULT_AGENT_URL + "/run-stream");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(300000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/x-ndjson");
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        if (code < 200 || code >= 300) {
            String response = readAll(stream);
            if (code == 409) {
                throw new IllegalStateException("агент занят, нажми STATE и повтори позже");
            }
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }

        String finalMessage = "";
        boolean ok = true;
        boolean cancelled = false;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                String clean = line.trim();
                if (clean.isEmpty()) {
                    continue;
                }
                JSONObject event = new JSONObject(clean);
                String type = event.optString("type", "");
                if ("final".equals(type)) {
                    finalMessage = event.optString("message", "").trim();
                    cancelled = event.optBoolean("cancelled", false);
                    ok = cancelled || event.optBoolean("ok", true);
                } else if ("error".equals(type)) {
                    ok = false;
                    finalMessage = event.optString("message", "Ошибка агента");
                }
                main.post(() -> handleAgentEvent(event));
            }
        }
        if (!ok) {
            throw new IllegalStateException(finalMessage.isEmpty() ? "agent stream failed" : finalMessage);
        }
        if (cancelled && finalMessage.isEmpty()) {
            return "Агент остановлен: задача отменена.";
        }
        return finalMessage.isEmpty() ? "Агент вернул пустой ответ." : finalMessage;
    }

    private String requestAgentCancel(String taskId) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("task_id", taskId);
        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(DEFAULT_AGENT_URL + "/cancel");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/json");
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

    private String requestAgentRun(String task) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("task", task);
        payload.put("technical", true);
        payload.put("max_steps", 12);
        payload.put("memory_namespace", "agent");
        payload.put("archive_task", true);
        payload.put("task_memory", true);
        payload.put("include_steps", false);
        payload.put("include_stderr", false);
        payload.put("shell_enabled", false);
        payload.put("wait_for_slot", false);

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(DEFAULT_AGENT_URL + "/run");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(240000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "application/json");
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int code = conn.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) {
            if (code == 409) {
                throw new IllegalStateException("агент занят, нажми STATE и повтори позже");
            }
            throw new IllegalStateException("HTTP " + code + ": " + response);
        }
        JSONObject json = new JSONObject(response);
        String message = json.optString("message", "").trim();
        if (!json.optBoolean("ok", false)) {
            throw new IllegalStateException(message.isEmpty() ? response : message);
        }
        return message.isEmpty() ? "Агент вернул пустой ответ." : message;
    }

    private String requestAgentState() throws Exception {
        URL url = new URL(DEFAULT_AGENT_URL + "/state");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("Accept", "application/json");

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
            out.append("\nRevision: ").append(revision);
        }
        out.append("\nОчередь: ").append(state.optInt("queued", 0));
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
        button.setText("STOP");
        speechStatus.setText(titleText + ": слушаю и сразу отправляю...");

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
            });
        } catch (Exception exc) {
            main.post(() -> {
                resetSpeechButton();
                speechStatus.setText("STT ошибка: " + exc.getMessage());
            });
        }
    }

    private String requestRemoteSttLive(String language, String titleText) throws Exception {
        URL url = new URL(DEFAULT_STT_URL + "/stt-live");
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
        URL url = new URL(DEFAULT_STT_URL + "/stt-pcm");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(240000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/octet-stream");
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("X-Language", language);
        conn.setRequestProperty("X-Sample-Rate", String.valueOf(AUDIO_SAMPLE_RATE));
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
            activeSpeechButton.setText(activeSpeechButton == speechButton ? "REC " + TRANSLATOR_SHORT[translatorSourceIndex] : "REC");
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
        scrim.setBackgroundColor(Color.argb(150, 0, 0, 0));
        scrim.setAlpha(0f);
        scrim.setVisibility(View.GONE);
        root.addView(scrim, new FrameLayout.LayoutParams(-1, -1));
        scrim.setOnClickListener(v -> setDrawerOpen(false));

        int drawerWidth = Math.min(dp(316), getResources().getDisplayMetrics().widthPixels - dp(52));
        drawer = new LinearLayout(this);
        drawer.setOrientation(LinearLayout.VERTICAL);
        drawer.setPadding(dp(18), dp(26), dp(18), dp(18));
        drawer.setBackground(drawerBackground());
        drawer.setTranslationX(-drawerWidth);
        FrameLayout.LayoutParams drawerLp = new FrameLayout.LayoutParams(drawerWidth, -1, Gravity.LEFT);
        root.addView(drawer, drawerLp);

        TextView name = new TextView(this);
        name.setText("Шушуня");
        name.setTextColor(Color.rgb(244, 217, 137));
        name.setTextSize(25);
        name.setTypeface(Typeface.DEFAULT_BOLD);
        drawer.addView(name, new LinearLayout.LayoutParams(-1, dp(50)));

        drawerChat = drawerItem("Шушуня");
        drawerTranslator = drawerItem("Переводчик");
        drawerAgent = drawerItem("Агент");
        drawer.addView(drawerChat);
        drawer.addView(drawerTranslator);
        drawer.addView(drawerAgent);

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
        updateDrawerSelection();
    }

    private TextView drawerItem(String text) {
        TextView item = new TextView(this);
        item.setText(text);
        item.setTextSize(18);
        item.setTypeface(Typeface.DEFAULT_BOLD);
        item.setGravity(Gravity.CENTER_VERTICAL);
        item.setPadding(dp(16), 0, dp(16), 0);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(54));
        lp.topMargin = dp(10);
        item.setLayoutParams(lp);
        return item;
    }

    private void showTab(String tab) {
        currentTab = tab;
        boolean chat = TAB_CHAT.equals(tab);
        boolean translator = TAB_TRANSLATOR.equals(tab);
        boolean agent = TAB_AGENT.equals(tab);
        title.setText(chat ? "Шушуня" : agent ? "Агент" : "Переводчик");
        endpoint.setText(agent ? DEFAULT_AGENT_URL : baseUrl);
        endpoint.setVisibility((chat || agent) ? View.VISIBLE : View.INVISIBLE);
        chatView.setVisibility(chat ? View.VISIBLE : View.GONE);
        translatorView.setVisibility(translator ? View.VISIBLE : View.GONE);
        agentView.setVisibility(agent ? View.VISIBLE : View.GONE);
        if (chat) {
            translatorView.setPadding(0, dp(10), 0, 0);
            agentView.setPadding(0, dp(6), 0, 0);
            scrollView.setPadding(0, 0, 0, lastKeyboardHeight > 0 ? lastKeyboardHeight + inputPanel.getHeight() + dp(14) : 0);
            float lift = lastKeyboardHeight > 0 ? -lastKeyboardHeight + dp(10) : 0f;
            inputPanel.animate().translationY(lift).setDuration(120).start();
        } else if (agent) {
            inputPanel.animate().translationY(0f).setDuration(120).start();
            scrollView.setPadding(0, 0, 0, 0);
            updateAgentKeyboardLift();
        } else {
            inputPanel.animate().translationY(0f).setDuration(120).start();
            scrollView.setPadding(0, 0, 0, 0);
            updateToolKeyboardPadding();
        }
        updateDrawerSelection();
    }

    private void updateToolKeyboardPadding() {
        int bottom = lastKeyboardHeight > 0 ? lastKeyboardHeight + dp(10) : 0;
        if (translatorView != null) {
            translatorView.setPadding(0, dp(10), 0, bottom);
        }
    }

    private void updateAgentKeyboardLift() {
        if (agentInputPanel == null || agentScrollView == null) {
            return;
        }
        agentInputPanel.post(() -> {
            float lift = lastKeyboardHeight > 0 ? -lastKeyboardHeight + dp(10) : 0f;
            int bottomPadding = lastKeyboardHeight > 0 ? lastKeyboardHeight + agentInputPanel.getHeight() + dp(14) : 0;
            agentScrollView.setPadding(0, 0, 0, bottomPadding);
            agentInputPanel.animate()
                    .translationY(lift)
                    .setDuration(120)
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
            maybeScrollAgentToBottom(false);
        });
    }

    private void updateDrawerSelection() {
        styleDrawerItem(drawerChat, TAB_CHAT.equals(currentTab));
        styleDrawerItem(drawerTranslator, TAB_TRANSLATOR.equals(currentTab));
        styleDrawerItem(drawerAgent, TAB_AGENT.equals(currentTab));
    }

    private void styleDrawerItem(TextView item, boolean selected) {
        item.setTextColor(selected ? Color.rgb(5, 13, 31) : Color.rgb(230, 240, 245));
        item.setBackground(selected
                ? pill(Color.rgb(201, 156, 58), Color.rgb(29, 191, 183), dp(16))
                : pill(Color.rgb(12, 30, 60), Color.rgb(48, 84, 116), dp(16)));
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
        grid.setBackgroundColor(Color.rgb(5, 12, 31));
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
                window.setBackgroundDrawable(pill(Color.rgb(8, 17, 43), Color.rgb(201, 156, 58), dp(14)));
            }
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setTextColor(Color.rgb(201, 156, 58));
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
                selectedImagePreview.setVisibility(View.VISIBLE);
                updateChatKeyboardLift();
                attachImage.animate().alpha(1f).setDuration(120).start();
                attachImage.setColorFilter(Color.rgb(201, 156, 58));
            });
        } catch (Exception exc) {
            main.post(() -> {
                pendingImageDataUrl = null;
                pendingImageLabel = null;
                pendingImagePreview = null;
                selectedImagePreview.setVisibility(View.GONE);
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
        selectedImagePreview.setVisibility(View.GONE);
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
                        Color.rgb(7, 17, 48),
                        Color.rgb(4, 9, 29),
                        Color.rgb(9, 25, 51)
                });
    }

    private GradientDrawable drawerBackground() {
        GradientDrawable drawable = new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{
                        Color.rgb(7, 18, 46),
                        Color.rgb(10, 23, 50)
                });
        drawable.setStroke(dp(1), Color.rgb(201, 156, 58));
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

        input.setText("");
        pendingImageDataUrl = null;
        pendingImageLabel = null;
        pendingImagePreview = null;
        selectedImagePreview.setImageDrawable(null);
        selectedImagePreview.setVisibility(View.GONE);
        resetAttachImageButton();
        if (hasImage) {
            addImageMessage(text, imagePreview, imageLabel);
        } else {
            addMessage(true, text);
        }
        TextView answerBubble = addMessage(false, "", false);
        setWaiting(true);
        startAnswerKeepAlive();

        new Thread(() -> {
            PowerManager.WakeLock wakeLock = acquireAnswerWakeLock();
            try {
                StreamingBubble liveBubble = new StreamingBubble(answerBubble);
                streamAnswer(text, imageDataUrl, liveBubble);
                main.post(() -> setWaiting(false));
            } catch (Exception e) {
                main.post(() -> {
                    setWaiting(false);
                    String error = "Связь сорвалась: " + e.getMessage();
                    answerBubble.setText(error);
                    saveChatMessage(false, error);
                    showAnswerNotification(error);
                    maybeScrollToBottom(false);
                });
            } finally {
                if (wakeLock != null && wakeLock.isHeld()) {
                    wakeLock.release();
                }
                stopAnswerKeepAlive();
            }
        }).start();
    }

    private void startAnswerKeepAlive() {
        synchronized (keepAliveLock) {
            activeKeepAliveJobs += 1;
            if (activeKeepAliveJobs > 1) {
                return;
            }
        }
        Intent intent = new Intent(this, KeepAliveService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
    }

    private void stopAnswerKeepAlive() {
        synchronized (keepAliveLock) {
            if (activeKeepAliveJobs > 0) {
                activeKeepAliveJobs -= 1;
            }
            if (activeKeepAliveJobs > 0) {
                return;
            }
        }
        stopService(new Intent(this, KeepAliveService.class));
    }

    private void resetAttachImageButton() {
        attachImage.animate().alpha(1f).setDuration(120).start();
        attachImage.setColorFilter(Color.rgb(244, 217, 137));
    }

    private void streamAnswer(String text, String imageDataUrl, StreamingBubble liveBubble) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("model", MODEL);
        payload.put("user", "redmagic9-shushunya-m");
        payload.put("archive_enabled", true);
        payload.put("focus_enabled", true);
        payload.put("max_tokens", 2048);
        payload.put("temperature", 0.4);
        payload.put("stream", true);

        JSONArray arr = new JSONArray();
        arr.put(new JSONObject().put("role", "system").put("content", SYSTEM_PROMPT));
        arr.put(new JSONObject().put("role", "user").put("content", userContent(text, imageDataUrl)));
        payload.put("messages", arr);

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        URL url = new URL(trimSlash(baseUrl) + "/v1/chat/completions");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(180000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "text/event-stream");
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

    private TextView addAgentMessage(boolean fromUser, String text, boolean animate) {
        TextView bubble = new TextView(this);
        bubble.setText(text);
        bubble.setTextSize(16);
        bubble.setLineSpacing(dp(2), 1.0f);
        bubble.setTextColor(fromUser ? Color.rgb(247, 240, 221) : Color.rgb(224, 250, 247));
        bubble.setPadding(dp(14), dp(10), dp(14), dp(10));
        bubble.setBackground(fromUser
                ? pill(Color.rgb(78, 43, 105), Color.rgb(205, 160, 61), dp(18))
                : pill(Color.rgb(9, 35, 57), Color.rgb(33, 190, 181), dp(18)));
        bubble.setAlpha(animate ? 0f : 1f);
        bubble.setTranslationY(animate ? dp(10) : 0f);

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels - dp(74), dp(560)),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = fromUser ? Gravity.RIGHT : Gravity.LEFT;
        lp.topMargin = dp(6);
        lp.bottomMargin = dp(6);
        agentMessageList.addView(bubble, lp);

        if (animate) {
            bubble.animate()
                    .alpha(1f)
                    .translationY(0f)
                    .setDuration(210)
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
        }
        maybeScrollAgentToBottom(true);
        return bubble;
    }

    private void addImageMessage(String text, Bitmap image, String fallbackLabel) {
        LinearLayout bubble = new LinearLayout(this);
        bubble.setOrientation(LinearLayout.VERTICAL);
        bubble.setPadding(dp(8), dp(8), dp(8), dp(8));
        bubble.setBackground(pill(Color.rgb(78, 43, 105), Color.rgb(205, 160, 61), dp(18)));
        bubble.setAlpha(0f);
        bubble.setTranslationY(dp(10));

        if (image != null) {
            ImageView imageView = new ImageView(this);
            imageView.setImageBitmap(image);
            imageView.setScaleType(ImageView.ScaleType.CENTER_CROP);
            imageView.setBackground(pill(Color.rgb(6, 14, 36), Color.rgb(201, 156, 58), dp(14)));
            imageView.setPadding(dp(2), dp(2), dp(2), dp(2));
            int width = Math.min(getResources().getDisplayMetrics().widthPixels - dp(96), dp(430));
            int height = Math.max(dp(160), Math.min(dp(320), Math.round(width * image.getHeight() / Math.max(1f, image.getWidth()))));
            bubble.addView(imageView, new LinearLayout.LayoutParams(-1, height));
        }

        if (text != null && !text.trim().isEmpty()) {
            TextView caption = new TextView(this);
            caption.setText(text.trim());
            caption.setTextSize(16);
            caption.setLineSpacing(dp(2), 1.0f);
            caption.setTextColor(Color.rgb(247, 240, 221));
            caption.setPadding(dp(6), dp(8), dp(6), dp(2));
            bubble.addView(caption, new LinearLayout.LayoutParams(-1, -2));
            saveChatMessage(true, text);
        } else if (fallbackLabel != null && !fallbackLabel.trim().isEmpty()) {
            saveChatMessage(true, fallbackLabel);
        }

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels - dp(74), dp(560)),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = Gravity.RIGHT;
        lp.topMargin = dp(6);
        lp.bottomMargin = dp(6);
        messageList.addView(bubble, lp);

        bubble.animate()
                .alpha(1f)
                .translationY(0f)
                .setDuration(210)
                .setInterpolator(new DecelerateInterpolator())
                .start();
        maybeScrollToBottom(true);
    }

    private TextView addMessage(boolean fromUser, String text, boolean save) {
        TextView bubble = new TextView(this);
        bubble.setText(text);
        bubble.setTextSize(16);
        bubble.setLineSpacing(dp(2), 1.0f);
        bubble.setTextColor(fromUser ? Color.rgb(247, 240, 221) : Color.rgb(224, 250, 247));
        bubble.setPadding(dp(14), dp(10), dp(14), dp(10));
        bubble.setBackground(fromUser
                ? pill(Color.rgb(78, 43, 105), Color.rgb(205, 160, 61), dp(18))
                : pill(Color.rgb(9, 35, 57), Color.rgb(33, 190, 181), dp(18)));
        bubble.setAlpha(0f);
        bubble.setTranslationY(dp(10));

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels - dp(74), dp(560)),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = fromUser ? Gravity.RIGHT : Gravity.LEFT;
        lp.topMargin = dp(6);
        lp.bottomMargin = dp(6);
        messageList.addView(bubble, lp);

        bubble.animate()
                .alpha(1f)
                .translationY(0f)
                .setDuration(210)
                .setInterpolator(new DecelerateInterpolator())
                .start();
        maybeScrollToBottom(true);
        if (save) {
            saveChatMessage(fromUser, text);
        }
        return bubble;
    }

    private boolean restoreChatHistory() {
        String raw = getSharedPreferences(PREFS, MODE_PRIVATE).getString(CHAT_HISTORY_KEY, "");
        if (raw == null || raw.trim().isEmpty()) {
            return false;
        }
        try {
            JSONArray history = new JSONArray(raw);
            if (history.length() == 0) {
                return false;
            }
            for (int i = 0; i < history.length(); i++) {
                JSONObject item = history.optJSONObject(i);
                if (item == null) {
                    continue;
                }
                String role = item.optString("role", "");
                String text = item.optString("text", "");
                if (!text.isEmpty()) {
                    addMessage("user".equals(role), text, false);
                }
            }
            return true;
        } catch (Exception ignored) {
            return false;
        }
    }

    private void saveChatMessage(boolean fromUser, String text) {
        String clean = text == null ? "" : text.trim();
        if (clean.isEmpty()) {
            return;
        }
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        JSONArray history;
        try {
            history = new JSONArray(prefs.getString(CHAT_HISTORY_KEY, "[]"));
        } catch (Exception ignored) {
            history = new JSONArray();
        }
        JSONObject item = new JSONObject();
        try {
            item.put("role", fromUser ? "user" : "assistant");
            item.put("text", clean);
            history.put(item);
            while (history.length() > CHAT_HISTORY_LIMIT) {
                history.remove(0);
            }
            prefs.edit().putString(CHAT_HISTORY_KEY, history.toString()).apply();
        } catch (Exception ignored) {
        }
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
        if (!force && (chatTouchActive || userPinnedScroll)) {
            return;
        }
        if (force) {
            userPinnedScroll = false;
        }
        main.postDelayed(() -> {
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
        if (!force && (chatTouchActive || userPinnedScroll)) {
            return;
        }
        if (force) {
            userPinnedScroll = false;
        }
        main.postDelayed(() -> {
            int target = Math.max(0, agentMessageList.getBottom() + agentScrollView.getPaddingBottom() - agentScrollView.getHeight());
            agentScrollView.scrollTo(0, target);
        }, 60);
    }

    private void setWaiting(boolean value) {
        waiting = value;
        send.setEnabled(!value);
        send.animate().alpha(value ? 0.55f : 1f).setDuration(180).start();
        progress.setVisibility(value ? View.VISIBLE : View.GONE);
    }

    @Override
    protected void onDestroy() {
        recording = false;
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
            image.setBackground(pill(Color.rgb(6, 14, 36), Color.rgb(45, 82, 116), dp(10)));
            image.setPadding(dp(2), dp(2), dp(2), dp(2));
            try {
                Bitmap thumb = getContentResolver().loadThumbnail(images.get(position), new Size(dp(160), dp(160)), null);
                image.setImageBitmap(thumb);
            } catch (Exception ignored) {
                image.setImageResource(android.R.drawable.ic_menu_gallery);
                image.setColorFilter(Color.rgb(244, 217, 137));
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
                    } else {
                        bubble.setText(finished && shown >= available ? visible.trim() : visible + "▌");
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
