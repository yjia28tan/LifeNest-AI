"""Developer helper: run the spec section 15 demo questions through the chat API with the offline backends.

Usage: python scripts/demo_chat.py [demo_id]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

QUESTIONS = [
    "I'm looking at a RM750,000 condo in Cheras, can I afford it?",
    "Why so much lower than I thought?",
    "How much can I take from EPF?",
    "What if we have a child in three years?",
    "What would I need to do to reach RM650,000?",
    "Show me what I can actually afford near Kajang.",
    "Saya tengok kondo RM750,000 di Cheras, boleh saya mampu beli?",
    "Does the bank cover crypto income?",
]


def main() -> None:
    demo_id = sys.argv[1] if len(sys.argv) > 1 else "aina"
    settings = Settings(_env_file=None, app_env="dev", log_level="WARNING",
                        local_upload_dir=Path(tempfile.mkdtemp()))
    headers = {"Authorization": "Bearer dev:demo"}
    with TestClient(create_app(settings)) as client:
        sid = client.post("/sessions", headers=headers).json()["session_id"]
        client.post(f"/sessions/{sid}/demo-profile", headers=headers, json={"demo_id": demo_id})
        for q in QUESTIONS:
            r = client.post(f"/sessions/{sid}/messages", headers=headers, json={"message": q}).json()
            print("=" * 100)
            print("YOU:", q)
            print(r["reply"])
            print(f"  tools={[t['tool'] for t in r['tool_trace']]} grounding={r['grounding']['status']} "
                  f"cards={[c['type'] for c in r['cards']]}")


if __name__ == "__main__":
    main()
