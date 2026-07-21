package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.concurrent.atomic.AtomicInteger;

public final class AsyncResourceOwnerTest {
    @Test
    public void lateTaskCompletionAfterTimeoutClosesOrphanExactlyOnce() {
        AtomicInteger closes = new AtomicInteger();
        AsyncResourceOwner<String> owner = new AsyncResourceOwner<>(value -> closes.incrementAndGet());
        owner.abandon();
        owner.observe("late-channel");
        owner.observe("late-channel");
        assertEquals(1, closes.get());
        assertFalse(owner.claim("late-channel"));
        assertEquals(1, closes.get());
    }

    @Test
    public void claimedResourceIsNotClosedByLateSuccessListener() {
        AtomicInteger closes = new AtomicInteger();
        AsyncResourceOwner<String> owner = new AsyncResourceOwner<>(value -> closes.incrementAndGet());
        assertTrue(owner.claim("owned-output"));
        owner.observe("owned-output");
        assertEquals(0, closes.get());
    }
}
