import json
import os
import subprocess
import datetime
import glob

REPO_ROOT = os.getcwd()
STATE_FILE = os.path.join(REPO_ROOT, ".loop/sprint_info.json")
TRACE_FILE = os.path.join(REPO_ROOT, ".loop/trace.jsonl")
SNAPSHOT_FILE = os.path.join(REPO_ROOT, ".loop/current_state.md")

MAX_SPRINTS = 5
MAX_RETRIES = 3

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"current_sprint": 1, "phase": "IDLE", "retry_count": 0}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)
    with open(SNAPSHOT_FILE, "w") as f:
        f.write(f"Sprint: {state.get('current_sprint')}\n")
        f.write(f"Phase: {state.get('phase')}\n")
        f.write(f"Retry Count: {state.get('retry_count', 0)}\n")

def log_trace(step, sprint, status, details=""):
    trace = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "step": step, "sprint": sprint, "status": status, "details": details
    }
    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps(trace) + "\n")
    print(f"[{step}] {status} - {details}")

def load_context():
    """Aggregates all relevant files into a compacted context string."""
    context_files = [
        ".loop/context_summary.md",
        ".loop/backlog.md",
        ".loop/reports/inspection.md",
        ".loop/reports/test_report.md"
    ]
    context = ""
    for rel_f in context_files:
        abs_f = os.path.join(REPO_ROOT, rel_f)
        if os.path.exists(abs_f):
            with open(abs_f, "r") as file:
                context += f"\n--- {rel_f} ---\n" + file.read()
    return context

def run_cmd(cmd, cwd=None, ignore_error=False):
    """Robust command execution."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0 and not ignore_error:
        raise Exception(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout

def commit_state(sprint_num, phase):
    run_cmd(["git", "add", ".loop/", "published/"], cwd=REPO_ROOT)
    run_cmd(["git", "commit", "-m", f"chore(loop): State update - Sprint {sprint_num} {phase}"], cwd=REPO_ROOT, ignore_error=True)

def invoke_hermes(skill_name, instructions="", cwd=None):
    print(f"Calling Hermes Skill: {skill_name}")
    try:
        full_prompt = f"CONTEXT:\n{load_context()}\n\nINSTRUCTIONS:\n{instructions}"
        temp_file = os.path.join(REPO_ROOT, ".loop/temp_prompt.txt")
        with open(temp_file, "w") as f:
            f.write(full_prompt)
            
        result = subprocess.run(
            ["hermes", "--skill", skill_name, "--prompt-file", temp_file],
            cwd=cwd, capture_output=True, text=True
        )
        if result.returncode != 0:
             print(f"Hermes execution failed:\n{result.stderr}")
             return None
        return result.stdout
    except Exception as e:
        print(f"Execution error: {e}")
        return None

def validate_output(output, expected_marker):
    return expected_marker in (output or "")

def handle_retry_classification(state, output, sprint, role_name):
    """Smart routing based on failure type."""
    output_lower = (output or "").lower()
    
    if "missing requirement" in output_lower or "factual error" in output_lower:
        log_trace(role_name, sprint, "fail", "PM routing required")
        state["phase"] = "PM_PLANNING"
    else:
        log_trace(role_name, sprint, "fail", "Dev routing required")
        state["phase"] = "DEVELOPMENT"
        
    state["retry_count"] += 1

def main():
    state = load_state()
    phase = state.get("phase", "IDLE")
    sprint = state.get("current_sprint", 1)
    retries = state.get("retry_count", 0)

    if phase == "IDLE":
        print("Loop is idle.")
        return

    if sprint > MAX_SPRINTS:
        print("MAX_SPRINTS reached.")
        state["phase"] = "IDLE"
        save_state(state)
        return

    if retries > MAX_RETRIES:
        print("Stall detected. Hard routing back to PM Planning.")
        state["phase"] = "PM_PLANNING"
        state["retry_count"] = 0
        save_state(state)
        return

    print(f"\n=== Starting Sprint {sprint} | Phase: {phase} | Retry: {retries} ===")
    
    # Safe worktree location OUTSIDE the main repo to avoid recursive git issues
    worktree_path = os.path.abspath(os.path.join(REPO_ROOT, "..", f"loop_worktrees/sprint_{sprint}"))

    if phase == "PM_PLANNING":
        log_trace("PM", sprint, "started", "Planning sprint")
        output = invoke_hermes("pm_planner", "Read .loop/human_trigger.txt. Update .loop/backlog.md and compact context.", cwd=REPO_ROOT)
        if output:
            state["phase"] = "DEVELOPMENT"
            log_trace("PM", sprint, "done", "Backlog updated")
        else:
            state["phase"] = "ERROR"
        save_state(state)

    elif phase == "DEVELOPMENT":
        if not os.path.exists(worktree_path):
            run_cmd(["git", "worktree", "add", "-b", f"sprint_{sprint}", worktree_path], cwd=REPO_ROOT, ignore_error=True)
            os.makedirs(os.path.join(worktree_path, "drafts"), exist_ok=True)
            
        existing_drafts = glob.glob(os.path.join(worktree_path, f"drafts/post_s{sprint}_v*.md"))
        next_version = len(existing_drafts) + 1
        target_file = f"drafts/post_s{sprint}_v{next_version}.md"

        log_trace("DEV", sprint, "started", f"Drafting {target_file}")
        
        output = invoke_hermes("content_developer", 
            f"Write your output to exactly: {target_file}. Address all Inspector/Tester feedback.", 
            cwd=worktree_path) 
        
        if output:
            state["phase"] = "REVIEW"
            state["current_draft_file"] = target_file
            log_trace("DEV", sprint, "done", f"Draft {target_file} created")
        else:
            state["phase"] = "ERROR"
        save_state(state)

    elif phase == "REVIEW":
        log_trace("INSPECTOR", sprint, "started", "Reviewing")
        draft_to_review = state.get("current_draft_file", "")
        output = invoke_hermes("content_inspector", f"Review exactly this file: {draft_to_review}. Write feedback to {os.path.join(REPO_ROOT, '.loop/reports/inspection.md')}.", cwd=worktree_path)
        
        if not output:
             state["phase"] = "ERROR"
        elif validate_output(output, "STATUS: PASS"):
            log_trace("INSPECTOR", sprint, "pass", "Approved")
            state["phase"] = "TESTING"
            state["retry_count"] = 0
        else:
            handle_retry_classification(state, output, sprint, "INSPECTOR")
        save_state(state)

    elif phase == "TESTING":
        log_trace("TESTER", sprint, "started", "Linting")
        draft_to_test = state.get("current_draft_file", "")
        output = invoke_hermes("content_tester", f"Test exactly this file: {draft_to_test}. Write results to {os.path.join(REPO_ROOT, '.loop/reports/test_report.md')}.", cwd=worktree_path)
        
        if not output:
             state["phase"] = "ERROR"
        elif validate_output(output, "STATUS: PASS"):
            log_trace("TESTER", sprint, "pass", "Tests passed")
            state["phase"] = "PUBLISHING"
            state["retry_count"] = 0
        else:
            handle_retry_classification(state, output, sprint, "TESTER")
        save_state(state)
        
    elif phase == "PUBLISHING":
        log_trace("PUBLISHER", sprint, "started", "Publishing")
        approved_draft = state.get("current_draft_file")
        
        if approved_draft and os.path.exists(os.path.join(worktree_path, approved_draft)):
            run_cmd(["cp", approved_draft, os.path.join(REPO_ROOT, "published/")], cwd=worktree_path)
            log_trace("PUBLISHER", sprint, "done", f"Published {approved_draft}")
            
            run_cmd(["git", "add", "drafts/"], cwd=worktree_path, ignore_error=True)
            run_cmd(["git", "commit", "-m", f"chore: Finalized sprint {sprint} drafts"], cwd=worktree_path, ignore_error=True)
            
        run_cmd(["git", "worktree", "remove", "--force", worktree_path], cwd=REPO_ROOT, ignore_error=True)
        run_cmd(["git", "worktree", "prune"], cwd=REPO_ROOT, ignore_error=True)
            
        state["phase"] = "PM_PLANNING"
        state["current_sprint"] += 1
        # Clear the draft tracker for the next sprint
        state.pop("current_draft_file", None)
        save_state(state)
        commit_state(sprint, "COMPLETED")

if __name__ == "__main__":
    main()