package com.shushunya.m.wear.complication;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.drawable.Icon;

import androidx.annotation.Nullable;
import androidx.wear.watchface.complications.data.ComplicationData;
import androidx.wear.watchface.complications.data.ComplicationText;
import androidx.wear.watchface.complications.data.ComplicationType;
import androidx.wear.watchface.complications.data.MonochromaticImage;
import androidx.wear.watchface.complications.data.MonochromaticImageComplicationData;
import androidx.wear.watchface.complications.data.PlainComplicationText;
import androidx.wear.watchface.complications.data.ShortTextComplicationData;
import androidx.wear.watchface.complications.data.SmallImage;
import androidx.wear.watchface.complications.data.SmallImageComplicationData;
import androidx.wear.watchface.complications.data.SmallImageType;

import com.shushunya.m.R;
import com.shushunya.m.wear.control.MagicToggleActivity;
import com.shushunya.m.wear.control.WearActionReceiver;
import com.shushunya.m.wear.data.ControllerStateStore;

/** Builds complication data while keeping tap dispatch in one immutable broadcast action. */
final class ComplicationUiFactory {
    private ComplicationUiFactory() {}

    @Nullable
    static ComplicationData build(
            Context context,
            ControllerStateStore.Kind kind,
            ComplicationType type,
            boolean preview) {
        UiModel model = preview ? previewModel(kind) : currentModel(context, kind);
        PendingIntent tapAction = preview ? null : tapAction(context, kind);
        MonochromaticImage monochromaticImage = new MonochromaticImage.Builder(
                Icon.createWithResource(context, model.iconRes)).build();
        SmallImage smallImage = new SmallImage.Builder(
                Icon.createWithResource(context, model.iconRes),
                SmallImageType.ICON).build();
        ComplicationText description = text(model.description);

        if (type == ComplicationType.SHORT_TEXT) {
            ShortTextComplicationData.Builder builder = new ShortTextComplicationData.Builder(
                    text(model.text), description)
                    .setTitle(text(model.title))
                    .setMonochromaticImage(monochromaticImage)
                    .setSmallImage(smallImage);
            if (tapAction != null) builder.setTapAction(tapAction);
            return builder.build();
        }
        if (type == ComplicationType.MONOCHROMATIC_IMAGE) {
            MonochromaticImageComplicationData.Builder builder =
                    new MonochromaticImageComplicationData.Builder(
                            monochromaticImage, description);
            if (tapAction != null) builder.setTapAction(tapAction);
            return builder.build();
        }
        if (type == ComplicationType.SMALL_IMAGE) {
            SmallImageComplicationData.Builder builder =
                    new SmallImageComplicationData.Builder(smallImage, description);
            if (tapAction != null) builder.setTapAction(tapAction);
            return builder.build();
        }
        return null;
    }

    private static UiModel previewModel(ControllerStateStore.Kind kind) {
        if (kind == ControllerStateStore.Kind.LIVE) {
            return new UiModel(
                    "READY", "OFF", "Live translation is ready",
                    R.drawable.ic_hydra_exact);
        }
        return new UiModel(
                "MUSIC", "MUSIC", "Pause or resume music",
                R.drawable.ic_media_chaos_eye);
    }

    private static UiModel currentModel(Context context, ControllerStateStore.Kind kind) {
        ControllerStateStore.Snapshot state = ControllerStateStore.snapshot(context, kind);
        if (kind == ControllerStateStore.Kind.LIVE
                && state.livePhase != ControllerStateStore.LivePhase.NONE) {
            return livePhaseModel(state.livePhase);
        }
        if (state.pending) {
            if (kind == ControllerStateStore.Kind.LIVE) {
                return new UiModel(
                        "WAIT",
                        stableLiveTitle(state.confirmedState),
                        "Waiting for the phone; showing the last confirmed translator state",
                        stableLiveIcon(state.confirmedState));
            }
            return new UiModel(
                    "...", "MUSIC", "Sending the music command to the phone",
                    R.drawable.ic_state_pending);
        }
        if (state.timedOut || "offline".equals(state.transport)) {
            if (kind == ControllerStateStore.Kind.LIVE) {
                return new UiModel(
                        "OFFLINE",
                        stableLiveTitle(state.confirmedState),
                        "Phone is unavailable; showing the last confirmed translator state",
                        stableLiveIcon(state.confirmedState));
            }
            return new UiModel(
                    "OFFLINE", "MUSIC", "Phone is unavailable",
                    R.drawable.ic_state_error);
        }
        return kind == ControllerStateStore.Kind.LIVE
                ? liveModel(state)
                : musicModel(state);
    }

    private static UiModel livePhaseModel(ControllerStateStore.LivePhase phase) {
        if (phase == ControllerStateStore.LivePhase.STOPPING) {
            return new UiModel(
                    "STOP", "STOPPING", "Stopping live translation",
                    R.drawable.ic_chaos_exact);
        }
        return new UiModel(
                "START", "STARTING", "Starting live translation",
                R.drawable.ic_hydra_exact);
    }

    private static UiModel liveModel(ControllerStateStore.Snapshot state) {
        switch (state.state) {
            case RUNNING:
                String mic = firstNonEmpty(state.actualMic, state.selectedMic);
                return new UiModel(
                        "LIVE", "RUNNING",
                        appendDetail("Live translation is listening", mic, "Microphone"),
                        R.drawable.ic_chaos_exact);
            case PAUSED:
                return new UiModel(
                        "PAUSED", "OFF",
                        appendStatus("Live translation is paused", state.status),
                        R.drawable.ic_hydra_exact);
            case STOPPED:
                return new UiModel(
                        state.armed ? "READY" : "START",
                        "OFF",
                        state.armed
                                ? "Live translation is armed and ready"
                                : "Start live translation",
                        R.drawable.ic_hydra_exact);
            case ERROR:
                return new UiModel(
                        "ERROR",
                        stableLiveTitle(state.confirmedState),
                        appendStatus(
                                "Live translation error",
                                firstNonEmpty(state.error, state.status)),
                        stableLiveIcon(state.confirmedState));
            case UNKNOWN:
            default:
                return new UiModel(
                        "START", "OFF", "Toggle live translation",
                        R.drawable.ic_hydra_exact);
        }
    }

    private static UiModel musicModel(ControllerStateStore.Snapshot state) {
        switch (state.state) {
            case RUNNING:
                return new UiModel(
                        "PAUSE", "MUSIC",
                        appendDetail("Music is playing", state.title, "Track"),
                        R.drawable.ic_media_alpha_chain);
            case PAUSED:
                return new UiModel(
                        "PLAY", "MUSIC",
                        appendDetail("Music is paused", state.title, "Track"),
                        R.drawable.ic_media_chaos_eye);
            case ERROR:
                return new UiModel(
                        "ERROR", "MUSIC",
                        appendStatus("Could not toggle music", state.error),
                        R.drawable.ic_media_chaos_eye);
            case STOPPED:
            case UNKNOWN:
            default:
                return new UiModel(
                        "MUSIC", "MUSIC", "Pause or resume music",
                        R.drawable.ic_media_chaos_eye);
        }
    }

    private static String stableLiveTitle(ControllerStateStore.State confirmedState) {
        return isLiveOn(confirmedState) ? "RUNNING" : "OFF";
    }

    private static int stableLiveIcon(ControllerStateStore.State confirmedState) {
        return isLiveOn(confirmedState)
                ? R.drawable.ic_chaos_exact
                : R.drawable.ic_hydra_exact;
    }

    private static boolean isLiveOn(ControllerStateStore.State state) {
        return state == ControllerStateStore.State.RUNNING;
    }

    private static PendingIntent tapAction(Context context, ControllerStateStore.Kind kind) {
        String action = kind == ControllerStateStore.Kind.LIVE
                // A plain Data Layer LIVE_TOGGLE can reach a cold phone process,
                // but Android 14+ will not let that background callback create
                // the phone's foreground work. Route the visible Hydra tap
                // through the phone-owned MAGIC bridge instead: its remote
                // Activity is the user-visible launch authority that can cold
                // start Shushunya and pause/resume the current media session.
                ? WearActionReceiver.ACTION_MAGIC_TOGGLE
                : WearActionReceiver.ACTION_MUSIC_TOGGLE;
        // Samsung DWF can retain a wrapped PendingIntent across an in-place APK
        // update. Give both complications a new identity for every version so
        // neither button can keep calling a cancelled token from the old APK.
        int requestCode = tapRequestCode(context, kind);
        if (kind == ControllerStateStore.Kind.LIVE) {
            // Keep a visible user-launched Activity as the RemoteActivity
            // authority until the exact phone command becomes terminal.
            Intent intent = new Intent(context, MagicToggleActivity.class)
                    .setAction(action);
            return PendingIntent.getActivity(
                    context,
                    requestCode,
                    intent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        }
        Intent intent = new Intent(context, WearActionReceiver.class).setAction(action);
        return PendingIntent.getBroadcast(
                context,
                requestCode,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static int tapRequestCode(Context context, ControllerStateStore.Kind kind) {
        long versionCode = 0L;
        try {
            versionCode = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0)
                    .getLongVersionCode();
        } catch (PackageManager.NameNotFoundException ignored) {
            // This package owns the running provider, so this is only a
            // defensive fallback for broken test/package-manager contexts.
        }
        int generation = (int) (Math.max(0L, versionCode) % 100_000L);
        return 610_000 + generation * 2
                + (kind == ControllerStateStore.Kind.LIVE ? 1 : 2);
    }

    private static ComplicationText text(CharSequence value) {
        return new PlainComplicationText.Builder(value).build();
    }

    private static String appendStatus(String prefix, String status) {
        return status == null || status.trim().isEmpty()
                ? prefix
                : prefix + ". " + status.trim();
    }

    private static String appendDetail(String prefix, String detail, String label) {
        return detail == null || detail.trim().isEmpty()
                ? prefix
                : prefix + ". " + label + ": " + detail.trim();
    }

    private static String firstNonEmpty(String first, String second) {
        if (first != null && !first.trim().isEmpty()) return first;
        return second == null ? "" : second;
    }

    private static final class UiModel {
        final String text;
        final String title;
        final String description;
        final int iconRes;

        UiModel(String text, String title, String description, int iconRes) {
            this.text = text;
            this.title = title;
            this.description = description;
            this.iconRes = iconRes;
        }
    }
}
