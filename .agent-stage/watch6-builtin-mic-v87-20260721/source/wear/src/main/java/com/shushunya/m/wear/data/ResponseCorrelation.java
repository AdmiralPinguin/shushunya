package com.shushunya.m.wear.data;

final class ResponseCorrelation {
    private ResponseCorrelation() {}

    static boolean shouldApplyState(
            String pendingId,
            String authoritativeRequestId,
            String responseId) {
        // Empty IDs are asynchronous background snapshots and remain valid.
        if (isEmpty(responseId)) return true;
        // While a command is pending, only that exact command may mutate state.
        if (!isEmpty(pendingId)) return pendingId.equals(responseId);
        // Once pending clears, accept only a duplicate/final transition for the
        // most recently authoritative command. Delayed A must not overwrite B.
        return !isEmpty(authoritativeRequestId)
                && authoritativeRequestId.equals(responseId);
    }

    static boolean isMatchingCommandError(String pendingId, String responseId) {
        return !isEmpty(pendingId) && !isEmpty(responseId) && pendingId.equals(responseId);
    }

    static boolean shouldClearPending(
            String pendingId,
            long pendingAt,
            String responseId,
            long now,
            long timeoutMs) {
        if (isEmpty(pendingId)) return false;
        if (!isEmpty(responseId)) return pendingId.equals(responseId);
        return pendingAt > 0L && now - pendingAt > timeoutMs;
    }

    private static boolean isEmpty(String value) {
        return value == null || value.isEmpty();
    }
}
