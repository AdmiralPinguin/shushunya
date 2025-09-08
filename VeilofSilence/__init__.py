"""Veil of Silence module.

This package implements a simple gatekeeper for audio input.  Its job is to
enable or disable the microphone based on the state of the dialog manager
and high‑level user commands.  It does not attempt to interpret speech or
control audio playback – it simply decides whether the system should be
listening to the outside world.

The name "Veil of Silence" takes inspiration from the Warhammer 40k lore:
in the Immaterium, a veil of silence can fall over entire sectors, cutting
communication to nothing but static.  Here it serves as a poetic label for
the logic that mutes and unmutes the system's ears.
"""

__all__ = [
    "bus",
    "fsm",
    "run_veil_of_silence",
]