import sys, json, os

def trigger_loop(topic):
    os.makedirs(".loop", exist_ok=True)
    with open(".loop/human_trigger.txt", "w") as f: f.write(f"Topic: {topic}\n")
    
    state = json.load(open(".loop/sprint_info.json")) if os.path.exists(".loop/sprint_info.json") else {"current_sprint": 1, "retry_count": 0}
    state["phase"] = "PM_PLANNING"
    state["retry_count"] = 0
    json.dump(state, open(".loop/sprint_info.json", "w"), indent=4)
    print("Triggered. Run python3 scripts/orchestrator.py.")

if len(sys.argv) > 1: trigger_loop(sys.argv[1])