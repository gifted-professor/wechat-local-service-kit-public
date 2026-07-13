#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


APPLE_SCRIPT = r'''
on run argv
    if (count of argv) is less than 2 then error "expected search text and message text"
    set searchText to item 1 of argv
    set messageText to item 2 of argv

    tell application "WeChat"
        reopen
        activate
    end tell
    delay 0.8

    tell application "System Events"
        tell process "WeChat"
            set frontmost to true
        end tell

        set the clipboard to searchText
        keystroke "f" using command down
        delay 0.3
        keystroke "a" using command down
        delay 0.1
        key code 51
        delay 0.1
        keystroke "v" using command down
        delay 0.7
        key code 36
        delay 1.0

        set the clipboard to messageText
        keystroke "v" using command down
        delay 0.2
        key code 36
    end tell

    return "sent"
end run
'''

DRAFT_APPLE_SCRIPT = r'''
on run argv
    if (count of argv) is less than 2 then error "expected search text and message text"
    set searchText to item 1 of argv
    set messageText to item 2 of argv

    tell application "WeChat"
        reopen
        activate
    end tell
    delay 0.8

    tell application "System Events"
        tell process "WeChat"
            set frontmost to true
        end tell

        set the clipboard to searchText
        keystroke "f" using command down
        delay 0.3
        keystroke "a" using command down
        delay 0.1
        key code 51
        delay 0.1
        keystroke "v" using command down
        delay 0.7
        key code 36
        delay 1.0

        set the clipboard to messageText
        keystroke "v" using command down
        delay 0.2
    end tell

    return "drafted"
end run
'''


def send_text(search_text: str, message_text: str) -> dict:
    completed = subprocess.run(
        ["osascript", "-", search_text, message_text],
        input=APPLE_SCRIPT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def draft_text(search_text: str, message_text: str) -> dict:
    completed = subprocess.run(
        ["osascript", "-", search_text, message_text],
        input=DRAFT_APPLE_SCRIPT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one text message through WeChat UI by searching a conversation")
    parser.add_argument("--search", required=True, help="search text used in WeChat search box")
    parser.add_argument("--text", required=True, help="message text to send")
    parser.add_argument("--draft-only", action="store_true", help="paste text into the chat input box without pressing Enter")
    parser.add_argument("--settle", type=float, default=1.0, help="seconds to wait after osascript returns")
    args = parser.parse_args()

    result = draft_text(args.search, args.text) if args.draft_only else send_text(args.search, args.text)
    if result["returncode"] != 0:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return result["returncode"] or 1

    if args.settle > 0:
        time.sleep(args.settle)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
