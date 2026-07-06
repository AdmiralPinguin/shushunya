#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys


VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


def apostrophe_to_f5(text: str) -> str:
    return re.sub(rf"([{VOWELS}])'", r"+\1", text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--f5", action="store_true", help="Convert RUAccent apostrophe marks to F5 + marks.")
    parser.add_argument("text", nargs="*")
    args = parser.parse_args()
    text = " ".join(args.text) if args.text else sys.stdin.read()

    from ruaccent import load_accentor

    accentor = load_accentor(device="cpu")
    stressed = accentor(text)
    print(apostrophe_to_f5(stressed) if args.f5 else stressed)


if __name__ == "__main__":
    main()
