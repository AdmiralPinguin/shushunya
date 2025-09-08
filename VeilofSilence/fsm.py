"""Finite state machine for the Veil of Silence.

This module contains a simple class, :class:`VeilFSM`, which manages a
minimal set of states for controlling whether the system's microphone is
enabled or disabled.  It listens to events from the dialog manager and
user interface, and publishes control messages to the microphone input
module via ZeroMQ.  The FSM does *not* concern itself with audio output –
that is handled elsewhere in the system.  Its sole responsibility is
deciding whether to listen to the world or remain silent.

States
======

``IDLE``
    The default state.  In handsfree mode the microphone is enabled; in
    push‑to‑talk mode it is disabled.  Transitions into ``LISTEN`` or
    ``SPEAK_LOCK``.

``LISTEN``
    The microphone is actively enabled, either because the system is in
    handsfree mode and waiting for speech, or because the user is holding
    down the push‑to‑talk key.  Speech recognition (VAD/STT) should be
    active.  Transitions back to ``IDLE`` on push‑to‑talk release, or to
    ``SPEAK_LOCK`` when the dialog manager begins speaking.

``SPEAK_LOCK``
    The dialog manager has instructed the system to speak via TTS.  To
    avoid echoing the synthesised speech back into the recogniser, the
    microphone is disabled for a short "guard" period.  If ``barge_in`` is
    configured, the FSM can re‑enable the mic early upon detecting
    incoming speech and emit a ``BARGE_IN`` event.

``MUTED``
    A global mute has been engaged by the user.  The microphone remains
    disabled regardless of other states until unmuted.

"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .bus import Bus


class VOState:
    """Enumeration of Veil of Silence states."""
    IDLE = "IDLE"
    LISTEN = "LISTEN"
    SPEAK_LOCK = "SPEAK_LOCK"
    MUTED = "MUTED"


@dataclass
class VeilFSM:
    """Finite state machine controlling microphone gating.

    Parameters
    ----------
    cfg: dict
        Configuration dictionary loaded from YAML.  Expected keys:

        - ``barge_in`` (bool): Whether to attempt barge‑in detection during
          TTS.  Default ``True``.
        - ``barge_in_min_ms`` (int): Minimum active speech duration (in
          milliseconds) before a barge‑in is acknowledged.  Default ``120``.
        - ``speak_guard_ms`` (int): Duration (in milliseconds) after
          entering ``SPEAK_LOCK`` during which the mic remains disabled
          regardless of barge‑in events.  Default ``200``.
        - ``mode`` (str): ``"handsfree"`` or ``"push_to_talk"``.
        - ``mute`` (bool): If ``True`` at startup, the FSM enters the
          ``MUTED`` state and keeps the mic disabled.

    bus: Bus
        A bus instance used to publish microphone control commands and
        events.  Two topics are used:

        - ``io.mic``: Commands to the microphone input module.  Payloads
          have the form ``{"action":"enable"|"disable", "value":<optional>}``.
        - ``io.event``: Informational events, such as ``BARGE_IN``,
          ``MUTED``, ``UNMUTED`` or ``INTERRUPT``.  The payload includes
          the event type and a millisecond timestamp.
    """

    cfg: Dict[str, object]
    bus: Bus
    state: str = field(init=False)
    last_speak_ts: float = field(default=0.0, init=False)
    mode: str = field(init=False)

    def __post_init__(self) -> None:
        # Initialise mode and starting state
        self.mode = self.cfg.get("mode", "handsfree")
        if self.cfg.get("mute", False):
            self.state = VOState.MUTED
        else:
            self.state = VOState.IDLE
        # At startup, ensure microphone is enabled if we are allowed to
        if self.state != VOState.MUTED and self.mode == "handsfree":
            self._mic("enable")

    # Internal helpers -----------------------------------------------------

    def _mic(self, action: str, value: Optional[str] = None) -> None:
        """Publish a microphone control command."""
        msg = {"action": action}
        if value is not None:
            msg["value"] = value
        self.bus.send("io.mic", msg)

    def _event(self, ev: str) -> None:
        """Publish an informational event."""
        self.bus.send("io.event", {"type": ev, "ts": int(time.time() * 1000)})

    # Event handlers -------------------------------------------------------

    def handle_dm_state(self, data: Dict[str, object]) -> None:
        """React to state changes from the dialog manager.

        The dialog manager publishes ``dm.state`` events indicating its
        current high‑level status (e.g. ``SPEAK``, ``LISTEN``).  When the
        dialog manager enters the ``SPEAK`` state, the FSM transitions to
        ``SPEAK_LOCK`` and disables the microphone.  When the dialog
        manager leaves ``SPEAK``, the FSM returns to its idle or listening
        mode.
        """
        st = data.get("state")
        if st == "SPEAK":
            # Enter speak lock: disable mic and record timestamp
            self.state = VOState.SPEAK_LOCK
            self.last_speak_ts = time.time() * 1000.0
            self._mic("disable")
        else:
            # Leave speak lock: return to idle/listen if not muted
            if self.state == VOState.SPEAK_LOCK and self.state != VOState.MUTED:
                self.state = VOState.IDLE
                if self.mode == "handsfree" and self.state != VOState.MUTED:
                    self._mic("enable")

    def handle_ui_cmd(self, data: Dict[str, object]) -> None:
        """Process a UI command.

        Supported commands:

        - ``mute``: Enter the ``MUTED`` state and disable the mic.
        - ``unmute``: Exit the ``MUTED`` state and re‑enable the mic if
          appropriate.
        - ``push_to_talk_down``: In push‑to‑talk mode, enable the mic and
          enter ``LISTEN``.
        - ``push_to_talk_up``: In push‑to‑talk mode, disable the mic and
          return to ``IDLE``.
        - ``mode``: Switch between ``handsfree`` and ``push_to_talk``.
        - ``interrupt``: Signal an interrupt (e.g. a manual cut of TTS);
          this publishes an ``INTERRUPT`` event and returns to ``IDLE``.
        """
        cmd = data.get("cmd")
        if cmd == "mute":
            if self.state != VOState.MUTED:
                self.state = VOState.MUTED
                self._mic("disable")
                self._event("MUTED")
        elif cmd == "unmute":
            if self.state == VOState.MUTED:
                self.state = VOState.IDLE
                # Re-enable mic if handsfree
                if self.mode == "handsfree":
                    self._mic("enable")
                self._event("UNMUTED")
        elif cmd == "interrupt":
            # Manual interrupt; notify upstream that the user wants to stop
            # whatever is currently happening (e.g. cut off TTS).  We do
            # not control playback here, so simply emit the event and
            # reset our state to IDLE (unless muted).
            self._event("INTERRUPT")
            if self.state != VOState.MUTED:
                self.state = VOState.IDLE
                if self.mode == "handsfree":
                    self._mic("enable")
        elif cmd == "push_to_talk_down":
            if self.state != VOState.MUTED:
                self.mode = "push_to_talk"
                self._mic("enable")
                self.state = VOState.LISTEN
        elif cmd == "push_to_talk_up":
            if self.mode == "push_to_talk" and self.state != VOState.MUTED:
                self._mic("disable")
                self.state = VOState.IDLE
        elif cmd == "mode":
            val = data.get("value")
            if val in ("handsfree", "push_to_talk"):
                self.mode = val
                # Adjust mic based on new mode and current state
                if self.state == VOState.MUTED:
                    # Do nothing if muted
                    pass
                elif val == "handsfree":
                    # In handsfree we keep mic enabled when idle
                    if self.state in (VOState.IDLE, VOState.LISTEN):
                        self._mic("enable")
                else:
                    # In push_to_talk we keep mic disabled unless explicitly down
                    if self.state in (VOState.IDLE, VOState.LISTEN):
                        self._mic("disable")

    def handle_audio_in(self, topic: str, data: Dict[str, object]) -> None:
        """Handle incoming audio events for barge‑in detection.

        When ``barge_in`` is enabled and the FSM is in ``SPEAK_LOCK``,
        receiving any audio input (partial or final) after a short guard
        period will trigger a ``BARGE_IN`` event.  This indicates to the
        dialog manager that the user has started speaking and may wish to
        interrupt the current utterance.  The FSM re‑enables the mic when
        barge‑in occurs (if appropriate) but does not attempt to stop
        playback itself.
        """
        if self.state == VOState.SPEAK_LOCK and self.cfg.get("barge_in", True):
            now = time.time() * 1000.0
            guard_ms = int(self.cfg.get("speak_guard_ms", 200))
            # Do not allow barge-in within the guard period
            if now - self.last_speak_ts < guard_ms:
                return
            # If we receive any partial or final audio input, signal barge-in
            if topic.endswith(".partial") or topic.endswith(".final"):
                self._event("BARGE_IN")
                # Transition out of speak lock to listen for new utterance
                if self.state != VOState.MUTED:
                    self.state = VOState.IDLE
                    if self.mode == "handsfree":
                        self._mic("enable")