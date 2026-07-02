import json
import os
import subprocess
import datetime
import glob

# ── Paths ──────────────────────────────────────────────────────────────
REPO_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_DIR     = os.path.join(REPO_ROOT, ".loop")
STATE_FILE   = os.path.join(LOOP_DIR, "sprint_info.json")
TRACE_FILE   = os.path.join(LOOP_DIR, "trace.jsonl")
SNAPSHOT_FILE = os.path.join(LOOP_DIR, "current_state.md")

# ── Config ─────────────────────────────────────────────────────────────
TOTAL_SPRINTS    = 2
MAX_INNER_RETRIES = 2   # Dev→Inspector rounds before force-advancing


# ── State helpers ───────────────────────────────────────────────────────
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"current_sprint": 1, "phase": "IDLE", "inner_retry": 0, "topic": ""}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)
    # Human-readable live snapshot
    with open(SNAPSHOT_FILE, "w") as f:
        f.write(f"Sprint : {state.get('current_sprint')} / {TOTAL_SPRINTS}\n")
        f.write(f"Phase  : {state.get('phase')}\n")
        f.write(f"Retries: {state.get('inner_retry', 0)} / {MAX_INNER_RETRIES}\n")
        f.write(f"Topic  : {state.get('topic', '')}\n")


# ── Logging ─────────────────────────────────────────────────────────────
def log(step, sprint, status, details=""):
    entry = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "step": step, "sprint": sprint,
        "status": status, "details": details,
    }
    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  [{step}] {status} — {details}")


# ── Context builder ─────────────────────────────────────────────────────
def load_context(sprint):
    """
    Keep context intentionally small to avoid token overflow.
    Only the current sprint's goals + last inspector feedback.
    """
    files = {
        "sprint_goals": os.path.join(LOOP_DIR, f"sprint_{sprint}_goals.md"),
        "inspection":   os.path.join(LOOP_DIR, "inspection.md"),
    }
    ctx = f"TOPIC: {load_state().get('topic', '')}\n"
    for label, path in files.items():
        if os.path.exists(path):
            content = open(path).read().strip()
            # Hard cap per file to prevent overflow
            if len(content) > 800:
                content = content[:800] + "\n...[truncated]"
            ctx += f"\n--- {label} ---\n{content}\n"
    return ctx


# ── Hermes invoker with real-time streaming ─────────────────────────────
def invoke_hermes(skill_name, instructions, sprint):
    """
    Streams Hermes output line-by-line so you can see the model,
    token usage, and generated content in real time.
    """
    full_prompt = f"{load_context(sprint)}\n\nINSTRUCTIONS:\n{instructions}"

    print(f"\n{'─'*60}")
    print(f"  Hermes  skill={skill_name}")
    print(f"{'─'*60}")

    process = subprocess.Popen(
        ["hermes", "--skills", skill_name, "-z", full_prompt, "chat"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPO_ROOT,
    )

    output_lines = []
    for line in process.stdout:
        print(line, end="", flush=True)   # real-time stream
        output_lines.append(line)

    process.wait()

    stderr_out = process.stderr.read()
    if stderr_out:
        print(f"\n[hermes stderr]\n{stderr_out}")

    if process.returncode != 0:
        print(f"  !! Hermes exited with code {process.returncode}")
        return None

    return "".join(output_lines)


# ── Git commit ───────────────────────────────────────────────────────────
def git_commit(message):
    subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, capture_output=True)
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=REPO_ROOT, capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"  [git] committed: {message}")
    else:
        print(f"  [git] nothing to commit or error: {result.stderr.strip()}")


# ── Phase handlers ───────────────────────────────────────────────────────
def run_pm(state):
    sprint = state["current_sprint"]
    log("PM", sprint, "started", f"Planning sprint {sprint}")

    instructions = (
        f"You are the Product Manager for a newsletter about: {state['topic']}\n"
        f"This is Sprint {sprint} of {TOTAL_SPRINTS}.\n"
        f"Sprint 1 = research and outline.\n"
        f"Sprint 2 = write the first half of the article.\n"
        f"Sprint 3 = write the second half and a strong conclusion.\n\n"
        f"Define clear, concrete goals for Sprint {sprint} only. "
        f"Write them as a short bullet list to: "
        f".loop/sprint_{sprint}_goals.md"
    )

    output = invoke_hermes("pm_planner", instructions, sprint)
    if not output:
        state["phase"] = "ERROR"
        return

    # Save the PM output as the sprint goals
    goals_file = os.path.join(LOOP_DIR, f"sprint_{sprint}_goals.md")
    with open(goals_file, "w") as f:
        f.write(output.strip())

    log("PM", sprint, "done", f"Sprint {sprint} goals saved")
    state["phase"] = "DEVELOPMENT"
    state["inner_retry"] = 0


def run_dev(state):
    sprint = state["current_sprint"]
    retry  = state["inner_retry"]

    # Version = retry + 1 (v1 on first attempt, v2 on first retry, etc.)
    version     = retry + 1
    draft_file  = f"drafts/sprint_{sprint}_v{version}.md"
    abs_draft   = os.path.join(REPO_ROOT, draft_file)
    os.makedirs(os.path.join(REPO_ROOT, "drafts"), exist_ok=True)

    log("DEV", sprint, "started", f"Writing {draft_file}")

    # Tell the dev whether this is a fresh draft or a revision
    if retry == 0:
        task = (
            f"Write the content for Sprint {sprint} based on the sprint goals.\n"
            f"Use the Hermes web search tool to find real-time information.\n"
            f"Output the full markdown article content directly. Do NOT use file tools."
        )
    else:
        task = (
            f"Revise your previous draft based on the Inspector feedback in the context.\n"
            f"Address every ISSUE listed.\n"
            f"Output the full revised markdown content directly. Do NOT use file tools."
        )

    output = invoke_hermes("content_developer", task, sprint)
    if not output:
        state["phase"] = "ERROR"
        return

    # Save the developer's output as the draft
    with open(abs_draft, "w") as f:
        f.write(output.strip())

    log("DEV", sprint, "done", f"Draft saved to {draft_file}")
    state["phase"]         = "REVIEW"
    state["current_draft"] = draft_file


def run_inspector(state):
    sprint      = state["current_sprint"]
    draft_file  = state.get("current_draft", "")
    retry       = state["inner_retry"]

    log("INSPECTOR", sprint, "started", f"Reviewing {draft_file}")

    instructions = (
        f"Review the file: {draft_file}\n"
        f"Compare it against the sprint goals in the context.\n\n"
        f"RULES:\n"
        f"- PASS if the content addresses the sprint goals and is readable.\n"
        f"- FAIL only for missing core content or major factual errors.\n"
        f"- Do NOT fail for minor style or cosmetic issues.\n\n"
        f"Your ENTIRE response must end with EXACTLY one of:\n\n"
        f"STATUS: PASS\n\n"
        f"OR\n\n"
        f"STATUS: FAIL\n"
        f"ISSUES:\n"
        f"- <one line per blocking issue only>"
    )

    output = invoke_hermes("content_inspector", instructions, sprint)
    if not output:
        state["phase"] = "ERROR"
        return

    # Always save the inspector output for the developer's next context
    inspection_file = os.path.join(LOOP_DIR, "inspection.md")
    with open(inspection_file, "w") as f:
        f.write(output.strip())

    if "STATUS: PASS" in output:
        log("INSPECTOR", sprint, "pass", "Approved")
        state["phase"]       = "SPRINT_DONE"
        state["inner_retry"] = 0
    elif retry >= MAX_INNER_RETRIES:
        log("INSPECTOR", sprint, "force-pass",
            f"Max retries ({MAX_INNER_RETRIES}) reached — force advancing")
        state["phase"]       = "SPRINT_DONE"
        state["inner_retry"] = 0
    else:
        log("INSPECTOR", sprint, "fail", "Routing back to Developer")
        state["phase"]       = "DEVELOPMENT"
        state["inner_retry"] = retry + 1


def finish_sprint(state):
    sprint = state["current_sprint"]
    log("LOOP", sprint, "sprint-complete", f"Sprint {sprint} finished")

    git_commit(f"chore(loop): Sprint {sprint} complete")

    if sprint >= TOTAL_SPRINTS:
        # All sprints done — compile final document
        compile_final(state)
        state["phase"] = "DONE"
    else:
        state["current_sprint"] += 1
        state["phase"]          = "PM_PLANNING"
        state["inner_retry"]    = 0


def compile_final(state):
    """Merge all approved sprint drafts into published/final.md"""
    os.makedirs(os.path.join(REPO_ROOT, "published"), exist_ok=True)
    final_path = os.path.join(REPO_ROOT, "published", "final.md")

    drafts = []
    for sprint_n in range(1, TOTAL_SPRINTS + 1):
        # Pick highest version for each sprint
        pattern = os.path.join(REPO_ROOT, f"drafts/sprint_{sprint_n}_v*.md")
        matches = sorted(glob.glob(pattern))
        if matches:
            drafts.append(matches[-1])

    with open(final_path, "w") as out:
        for draft in drafts:
            out.write(open(draft).read().strip())
            out.write("\n\n---\n\n")

    log("PUBLISHER", TOTAL_SPRINTS, "done", f"Final article → published/final.md")
    git_commit("feat(loop): Publish final article")


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    os.makedirs(LOOP_DIR, exist_ok=True)
    state = load_state()
    phase = state.get("phase", "IDLE")

    if phase == "IDLE":
        print("Loop is idle. Run: python3 scripts/trigger.py \"<your topic>\"")
        return

    if phase == "DONE":
        print("Loop complete. See published/final.md")
        return

    sprint = state.get("current_sprint", 1)
    print(f"\n{'='*60}")
    print(f"  Sprint {sprint}/{TOTAL_SPRINTS}  |  Phase: {phase}  |  Retry: {state.get('inner_retry',0)}/{MAX_INNER_RETRIES}")
    print(f"{'='*60}")

    if   phase == "PM_PLANNING":  run_pm(state)
    elif phase == "DEVELOPMENT":  run_dev(state)
    elif phase == "REVIEW":       run_inspector(state)
    elif phase == "SPRINT_DONE":  finish_sprint(state)
    elif phase == "ERROR":
        print("Loop is in ERROR state. Run trigger.py to reset.")
        return
    else:
        print(f"Unknown phase: {phase}")
        return

    save_state(state)
    print(f"\n  Next phase → {state['phase']}")


if __name__ == "__main__":
    main()
