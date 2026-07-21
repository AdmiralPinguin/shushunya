package com.shushunya.m.wear.audio;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;

/** ADB-only visible trampoline for Android 14 microphone-FGS launch rules. */
public final class PowerConfRawDiagnosticActivity extends Activity {
    private static final String TAG = "ShushunyaPowerConfDiag";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        int seconds = getIntent() == null
                ? PowerConfRawDiagnosticService.DEFAULT_SECONDS_PER_SOURCE
                : getIntent().getIntExtra(
                        PowerConfRawDiagnosticService.EXTRA_SECONDS_PER_SOURCE,
                        PowerConfRawDiagnosticService.DEFAULT_SECONDS_PER_SOURCE);
        try {
            startForegroundService(new Intent(this, PowerConfRawDiagnosticService.class)
                    .setAction(PowerConfRawDiagnosticService.ACTION_CAPTURE)
                    .putExtra(PowerConfRawDiagnosticService.EXTRA_SECONDS_PER_SOURCE, seconds));
        } catch (RuntimeException failure) {
            Log.e(TAG, "Could not start raw diagnostic capture", failure);
        } finally {
            finish();
        }
    }
}
