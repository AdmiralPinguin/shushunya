package com.shushunya.m.wear.control;

import android.content.Context;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;

public final class Haptics {
    private Haptics() {}

    static void tick(Context context) {
        Vibrator vibrator = vibrator(context);
        if (vibrator != null && vibrator.hasVibrator()) {
            vibrator.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK));
        }
    }

    static void failure(Context context) {
        Vibrator vibrator = vibrator(context);
        if (vibrator != null && vibrator.hasVibrator()) {
            vibrator.vibrate(VibrationEffect.createWaveform(
                    new long[] { 0L, 65L, 55L, 65L }, -1));
        }
    }

    static void success(Context context) {
        Vibrator vibrator = vibrator(context);
        if (vibrator != null && vibrator.hasVibrator()) {
            vibrator.vibrate(VibrationEffect.createWaveform(
                    new long[] { 0L, 35L, 35L, 80L }, -1));
        }
    }

    /** Separate strong confirmation for exact final RUNNING + engaged state. */
    public static void strongSuccess(Context context) {
        Vibrator vibrator = vibrator(context);
        if (vibrator != null && vibrator.hasVibrator()) {
            vibrator.vibrate(VibrationEffect.createPredefined(
                    VibrationEffect.EFFECT_HEAVY_CLICK));
        }
    }

    private static Vibrator vibrator(Context context) {
        VibratorManager manager = context.getSystemService(VibratorManager.class);
        return manager == null ? null : manager.getDefaultVibrator();
    }
}
