#!/usr/bin/env python3
"""Run the Veil of Silence module.

This script launches the Veil of Silence, a simple gatekeeper for the
microphone.  It subscribes to events from the dialog manager, UI and
audio input modules via ZeroMQ and decides when the microphone should be
enabled or disabled.  It publishes control commands on the ``io.mic``
topic and high‑level events (e.g. ``BARGE_IN``) on the ``io.event`` topic.

To use this runner, prepare a YAML configuration file specifying at
minimum the ``zmq_bind_pub`` address, the list of ``zmq_connect_sub``
addresses to connect to and the list of ``topics_in`` to subscribe to.
Example configuration::

    zmq_bind_pub: "tcp://127.0.0.1:5582"
    zmq_connect_sub:
      - "tcp://127.0.0.1:5571"  # audio_in pub
      - "tcp://127.0.0.1:5591"  # dm_core pub
      - "tcp://127.0.0.1:5599"  # ui pub (optional)
    topics_in:
      - "audio.in.partial"
      - "audio.in.final"
      - "dm.state"
      - "ui.cmd"
    barge_in: true
    barge_in_min_ms: 120
    mode: "handsfree"        # or "push_to_talk"
    mute: false
    speak_guard_ms: 200

Place the configuration file alongside this script (e.g. ``config.yaml``)
or pass its path as the first argument when running.  For example::

    python3 -m veil_of_silence.run_veil_of_silence config.yaml

The script runs an event loop indefinitely until interrupted.  It does
not attempt to handle exceptions beyond logging and will exit on error.
"""

from __future__ import annotations

import sys
import time
import yaml

from .bus import Bus
from .fsm import VeilFSM


def main(cfg_path: str) -> None:
    # Load configuration from YAML file
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # Instantiate the bus
    bus = Bus(
        cfg["zmq_bind_pub"],
        cfg.get("zmq_connect_sub", []),
        cfg.get("topics_in", []),
    )

    # Create the FSM
    fsm = VeilFSM(cfg, bus)

    # Main event loop
    try:
        while True:
            topic, data = bus.recv(timeout_ms=100)
            if topic is None:
                # Sleep briefly to prevent busy looping when idle
                time.sleep(0.01)
                continue
            try:
                if topic == "dm.state":
                    fsm.handle_dm_state(data)
                elif topic == "ui.cmd":
                    fsm.handle_ui_cmd(data)
                elif topic.startswith("audio.in."):
                    fsm.handle_audio_in(topic, data)
                # Ignore other topics silently
            except Exception as exc:
                # Log the error and continue; consider integrating a
                # structured logger instead of print for production use
                print(f"[VeilOfSilence] Error handling message on {topic}: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("[VeilOfSilence] Interrupted; shutting down.")


if __name__ == "__main__":
    # Determine configuration path from argv or default location
    cfg = sys.argv[1] if len(sys.argv) > 1 else "veil_of_silence/config.yaml"
    main(cfg)