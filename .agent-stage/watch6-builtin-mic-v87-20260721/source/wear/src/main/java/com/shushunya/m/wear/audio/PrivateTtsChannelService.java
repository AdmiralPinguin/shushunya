package com.shushunya.m.wear.audio;

import android.content.Context;
import android.util.Log;

import com.google.android.gms.wearable.ChannelClient;
import com.google.android.gms.wearable.Node;
import com.google.android.gms.wearable.Wearable;
import com.google.android.gms.wearable.WearableListenerService;

/** Authenticated Data Layer entry point for phone-to-Watch private TTS PCM. */
public final class PrivateTtsChannelService extends WearableListenerService {
    private static final String TAG = "ShushunyaPrivateTts";

    @Override
    public void onChannelOpened(ChannelClient.Channel channel) {
        if (channel == null) return;
        String path = clean(channel.getPath());
        String nodeId = clean(channel.getNodeId());
        if (!PrivateTtsProtocol.CHANNEL_PATH.equals(path)) {
            reject(channel, "unexpected path=" + path);
            return;
        }
        if (nodeId.isEmpty() || !PrivateTtsTargetStore.matches(this, nodeId)) {
            reject(channel, "unexpected node=" + nodeId);
            return;
        }
        if (!PrivateTtsPlaybackService.isArmed(this)) {
            reject(channel, "SERVICE_NOT_ARMED node=" + nodeId);
            return;
        }
        if (!PrivateTtsPlaybackService.acceptChannel(this, channel)) {
            reject(channel, "playback owner unavailable node=" + nodeId);
            return;
        }
        Log.i(TAG, "Private TTS channel accepted node=" + nodeId);
    }

    @Override
    public void onPeerDisconnected(Node peer) {
        if (peer != null) {
            PrivateTtsPlaybackService.onPeerDisconnected(this, peer.getId());
        }
    }

    @Override
    public void onChannelClosed(
            ChannelClient.Channel channel,
            int closeReason,
            int appSpecificErrorCode) {
        Log.i(TAG, "Private TTS channel event closed node="
                + (channel == null ? "" : channel.getNodeId())
                + " reason=" + closeReason
                + " appError=" + appSpecificErrorCode);
    }

    @Override
    public void onDestroy() {
        // The process-owned playback service, not this short GMS callback service,
        // owns accepted channels and their streams.
        super.onDestroy();
    }

    private void reject(ChannelClient.Channel channel, String reason) {
        Log.w(TAG, "Rejecting private TTS channel: " + reason);
        closeChannel(getApplicationContext(), channel);
    }

    static void closeChannel(Context context, ChannelClient.Channel channel) {
        if (channel == null) return;
        try {
            Wearable.getChannelClient(context).close(channel)
                    .addOnFailureListener(error -> Log.w(
                            TAG, "Could not close private TTS channel", error));
        } catch (RuntimeException error) {
            Log.w(TAG, "Private TTS channel close threw", error);
        }
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
