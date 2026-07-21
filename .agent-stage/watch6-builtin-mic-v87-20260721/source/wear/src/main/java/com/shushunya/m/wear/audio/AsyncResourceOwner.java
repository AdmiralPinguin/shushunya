package com.shushunya.m.wear.audio;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Generation-independent owner for an asynchronous resource task. A resource
 * completing after timeout/cancellation is closed exactly once instead of
 * becoming a late Channel/OutputStream leak.
 */
final class AsyncResourceOwner<T> {
    interface Closer<T> {
        void close(T value);
    }

    private static final int PENDING = 0;
    private static final int OWNED = 1;
    private static final int ORPHAN = 2;

    private final Closer<T> closer;
    private final AtomicInteger state = new AtomicInteger(PENDING);
    private final AtomicReference<T> observed = new AtomicReference<>();
    private final AtomicBoolean closed = new AtomicBoolean(false);

    AsyncResourceOwner(Closer<T> closer) {
        if (closer == null) throw new IllegalArgumentException("closer == null");
        this.closer = closer;
    }

    void observe(T value) {
        if (value == null) return;
        observed.compareAndSet(null, value);
        if (state.get() == ORPHAN) closeOnce(value);
    }

    boolean claim(T value) {
        if (value == null) return false;
        observe(value);
        if (state.compareAndSet(PENDING, OWNED)) return true;
        if (state.get() == ORPHAN) closeOnce(value);
        return false;
    }

    void abandon() {
        int previous = state.getAndSet(ORPHAN);
        if (previous == PENDING || previous == ORPHAN) closeOnce(observed.get());
    }

    private void closeOnce(T value) {
        if (value != null && closed.compareAndSet(false, true)) closer.close(value);
    }
}
