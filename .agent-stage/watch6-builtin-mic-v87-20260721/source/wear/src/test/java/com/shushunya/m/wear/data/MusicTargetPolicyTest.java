package com.shushunya.m.wear.data;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

public final class MusicTargetPolicyTest {
    @Test
    public void selectsOnlyOneExactNearbyNode() {
        assertEquals("phone-1", MusicTargetPolicy.selectOneNearby(Arrays.asList(
                new MusicTargetPolicy.Candidate("phone-1", true),
                new MusicTargetPolicy.Candidate("cloud-node", false))));
    }

    @Test
    public void duplicateTopologyRowsForSameNodeRemainOneTarget() {
        assertEquals("phone-1", MusicTargetPolicy.selectOneNearby(Arrays.asList(
                new MusicTargetPolicy.Candidate("phone-1", true),
                new MusicTargetPolicy.Candidate("phone-1", true))));
    }

    @Test
    public void ambiguityAndNoNearbyNodeFailClosed() {
        assertEquals("", MusicTargetPolicy.selectOneNearby(Arrays.asList(
                new MusicTargetPolicy.Candidate("phone-1", true),
                new MusicTargetPolicy.Candidate("phone-2", true))));
        assertEquals("", MusicTargetPolicy.selectOneNearby(Collections.singletonList(
                new MusicTargetPolicy.Candidate("phone-1", false))));
        assertEquals("", MusicTargetPolicy.selectOneNearby(null));
    }
}
