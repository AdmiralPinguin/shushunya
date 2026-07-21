package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class StartupFrameBufferTest {
    @Test
    public void gracefulStopRetainsAndDrainsOneThroughFourCompleteStartupFrames() {
        for (int frameCount = 1; frameCount <= 4; frameCount++) {
            StartupFrameBuffer<Integer> frames = new StartupFrameBuffer<>(5);
            for (int sequence = 0; sequence < frameCount; sequence++) {
                frames.addLast(sequence);
            }
            for (int expected = 0; expected < frameCount; expected++) {
                assertEquals(expected, (int) frames.removeFirst());
            }
            assertTrue(frames.isEmpty());
        }
    }

    @Test(expected = IllegalStateException.class)
    public void capacityCannotSilentlyEvictACompleteStartupFrame() {
        StartupFrameBuffer<Integer> frames = new StartupFrameBuffer<>(2);
        frames.addLast(0);
        frames.addLast(1);
        frames.addLast(2);
    }
}
