package com.shushunya.m.wear.control;

import android.content.Context;

import com.shushunya.m.wear.data.ControllerStateStore;

/** Atomic SharedPreferences adapter for the exact durable coordinator state. */
final class MagicWakeCoordinatorStore {
    enum AcceptedResult { CHANGED, ALREADY_ACCEPTED, REJECTED }

    private static final Object LOCK = new Object();

    private MagicWakeCoordinatorStore() {}

    static boolean begin(
            Context context,
            String requestId,
            long issuedAtMs,
            long pendingTimeoutMs) {
        MagicWakeCoordinatorState candidate =
                MagicWakeCoordinatorState.discovering(requestId, issuedAtMs);
        if (!candidate.isValid()) return false;
        synchronized (LOCK) {
            MagicWakeCoordinatorState current = readLocked(context);
            if (current != null && !candidate.requestId.equals(current.requestId)) {
                return false;
            }
            if (current != null) return true;
            return ControllerStateStore.beginMagicPendingDurable(
                    context,
                    candidate.requestId,
                    pendingTimeoutMs,
                    candidate.encode());
        }
    }

    static MagicWakeCoordinatorState read(Context context) {
        synchronized (LOCK) {
            return readLocked(context);
        }
    }

    static boolean selectNodeExact(
            Context context,
            String requestId,
            String phoneNodeId,
            long wakeStartedAtMs) {
        synchronized (LOCK) {
            MagicWakeCoordinatorState current = readLocked(context);
            if (current == null
                    || current.phase != MagicWakeCoordinatorState.Phase.DISCOVERING
                    || !same(requestId, current.requestId)) return false;
            MagicWakeCoordinatorState selected =
                    current.withPhoneNode(phoneNodeId, wakeStartedAtMs);
            return selected.isValid() && writeLocked(context, selected);
        }
    }

    static boolean advanceExact(Context context, String requestId, int nextAttemptIndex) {
        synchronized (LOCK) {
            MagicWakeCoordinatorState current = readLocked(context);
            if (current == null || !same(requestId, current.requestId)) return false;
            return writeLocked(context, current.withNextAttempt(nextAttemptIndex));
        }
    }

    static AcceptedResult markAcceptedExact(
            Context context, String requestId, String phoneNodeId, boolean targetStart) {
        synchronized (LOCK) {
            MagicWakeCoordinatorState current = readLocked(context);
            if (current == null
                    || current.phase == MagicWakeCoordinatorState.Phase.DISCOVERING
                    || !same(requestId, current.requestId)
                    || !same(phoneNodeId, current.phoneNodeId)) {
                return AcceptedResult.REJECTED;
            }
            if (current.isAccepted()) return AcceptedResult.ALREADY_ACCEPTED;
            return writeLocked(context, current.accepted(targetStart))
                    ? AcceptedResult.CHANGED
                    : AcceptedResult.REJECTED;
        }
    }

    static boolean markAcceptedActionConsumedExact(
            Context context, String requestId, String phoneNodeId, boolean targetStart) {
        synchronized (LOCK) {
            MagicWakeCoordinatorState current = readLocked(context);
            if (current == null
                    || !current.isAccepted()
                    || !same(requestId, current.requestId)
                    || !same(phoneNodeId, current.phoneNodeId)
                    || current.targetStart != targetStart) return false;
            if (current.acceptedActionConsumed) return true;
            return writeLocked(context, current.acceptedActionConsumed());
        }
    }

    static boolean clearExact(Context context, String requestId) {
        synchronized (LOCK) {
            MagicWakeCoordinatorState current = readLocked(context);
            if (current == null || !same(requestId, current.requestId)) return false;
            return ControllerStateStore.clearMagicWakeCoordinatorState(context);
        }
    }

    private static MagicWakeCoordinatorState readLocked(Context context) {
        return MagicWakeCoordinatorState.decode(
                ControllerStateStore.readMagicWakeCoordinatorState(context));
    }

    private static boolean writeLocked(
            Context context, MagicWakeCoordinatorState state) {
        String encoded = state == null ? "" : state.encode();
        return !encoded.isEmpty()
                && ControllerStateStore.writeMagicWakeCoordinatorState(
                        context, encoded);
    }

    private static boolean same(String left, String right) {
        String cleanLeft = left == null ? "" : left.trim();
        String cleanRight = right == null ? "" : right.trim();
        return !cleanLeft.isEmpty() && cleanLeft.equals(cleanRight);
    }
}
