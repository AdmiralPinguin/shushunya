package com.shushunya.m.wear.audio;

import java.util.ArrayDeque;

/** Bounded owner of complete pre-anchor frames; graceful stop drains all of it. */
final class StartupFrameBuffer<T> {
    private final int capacity;
    private final ArrayDeque<T> frames;

    StartupFrameBuffer(int capacity) {
        if (capacity <= 0) throw new IllegalArgumentException("capacity <= 0");
        this.capacity = capacity;
        frames = new ArrayDeque<>(capacity);
    }

    void addLast(T frame) {
        if (frame == null) throw new IllegalArgumentException("frame == null");
        if (frames.size() >= capacity) {
            throw new IllegalStateException("startup frame capacity exceeded");
        }
        frames.addLast(frame);
    }

    T removeFirst() {
        return frames.removeFirst();
    }

    int size() {
        return frames.size();
    }

    boolean isEmpty() {
        return frames.isEmpty();
    }
}
