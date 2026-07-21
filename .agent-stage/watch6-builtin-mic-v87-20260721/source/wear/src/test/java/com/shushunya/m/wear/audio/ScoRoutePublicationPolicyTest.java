package com.shushunya.m.wear.audio;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

import java.util.List;

public final class ScoRoutePublicationPolicyTest {
    @Test
    public void waitsUntilOneExactOrRedactedScoEndpointAppears() {
        assertEquals(
                ScoRoutePublicationPolicy.State.MISSING,
                ScoRoutePublicationPolicy.evaluate(
                        List.of(), PowerConfScoSessionPolicy.EXPECTED_ADDRESS));
        assertEquals(
                ScoRoutePublicationPolicy.State.READY,
                ScoRoutePublicationPolicy.evaluate(
                        List.of(PowerConfScoSessionPolicy.EXPECTED_ADDRESS),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS));
        assertEquals(
                ScoRoutePublicationPolicy.State.READY,
                ScoRoutePublicationPolicy.evaluate(
                        List.of(""), PowerConfScoSessionPolicy.EXPECTED_ADDRESS));
    }

    @Test
    public void ambiguousAndWrongScoRoutesNeverBecomeReady() {
        assertEquals(
                ScoRoutePublicationPolicy.State.AMBIGUOUS,
                ScoRoutePublicationPolicy.evaluate(
                        List.of("", ""), PowerConfScoSessionPolicy.EXPECTED_ADDRESS));
        assertEquals(
                ScoRoutePublicationPolicy.State.ADDRESS_MISMATCH,
                ScoRoutePublicationPolicy.evaluate(
                        List.of("AA:BB:CC:DD:EE:FF"),
                        PowerConfScoSessionPolicy.EXPECTED_ADDRESS));
    }
}
