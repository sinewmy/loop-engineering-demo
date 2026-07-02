import os
import json
import shutil

REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_DIR   = os.path.join(REPO_ROOT, ".loop")
STATE_FILE = os.path.join(LOOP_DIR, "sprint_info.json")


def cleanup():
    print("Cleaning up loop state...")

    # Remove state, drafts, published
    for d in [".loop", "drafts", "published"]:
        path = os.path.join(REPO_ROOT, d)
        if os.path.exists(path):
            shutil.rmtree(path)
            print(f"  Removed {d}/")

    # Re-create .loop with a clean IDLE state
    os.makedirs(LOOP_DIR, exist_ok=True)
    state = {"current_sprint": 1, "phase": "IDLE", "inner_retry": 0, "topic": ""}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

    print("  Created fresh .loop/sprint_info.json (IDLE)")
    print("\nCleanup complete. Ready for a fresh trigger.")


if __name__ == "__main__":
    cleanup()
