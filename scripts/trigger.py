import sys
import json
import os

REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_DIR   = os.path.join(REPO_ROOT, ".loop")
STATE_FILE = os.path.join(LOOP_DIR, "sprint_info.json")


def trigger_loop(topic):
    os.makedirs(LOOP_DIR, exist_ok=True)

    state = {
        "current_sprint": 1,
        "phase": "PM_PLANNING",
        "inner_retry": 0,
        "topic": topic,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

    print(f"Loop triggered!")
    print(f"Topic  : {topic}")
    print(f"Sprints: 3")
    print(f"")
    print(f"Run: python3 scripts/orchestrator.py")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python3 scripts/trigger.py "<Your Topic>"')
        sys.exit(1)
    trigger_loop(sys.argv[1])
