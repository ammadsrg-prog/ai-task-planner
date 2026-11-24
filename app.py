import json
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import streamlit as st

import governance
from agent import approve, chat, reject
from bedrock import cost_summary
from planner import WEEKDAYS, create_goal, goal_progress, goal_tasks, replan_goal, week_stats, weekly_review
from storage import add_task, complete_task, delete_goal, delete_task, list_goals, list_tasks

st.set_page_config(page_title="AI Task Planner", page_icon="✅", layout="wide")
st.title("✅ AI Task Planner")

# st.session_state keeps values while the page is open (Streamlit re-runs this file on every click)
if "history" not in st.session_state:
    st.session_state.history = []   # The agent's memory (sent to Bedrock)
if "messages" not in st.session_state:
    st.session_state.messages = []  # What we show on screen: (who, text)
if "pending" not in st.session_state:
    st.session_state.pending = []   # Risky AI actions waiting for the user's approval

ICONS = {"high": "🔴", "medium": "🟡", "low": "🟢"}
goal_names = {g["id"]: g["title"] for g in list_goals()}


def show_task_row(t, key_prefix):
    """One task: tick box, title, due date, delete button."""
    c1, c2, c3 = st.columns([0.1, 0.75, 0.15])
    ticked = c1.checkbox("done", value=t["done"], key=f"{key_prefix}_done_{t['id']}",
                         label_visibility="collapsed", disabled=t["done"])
    if ticked and not t["done"]:  # Only when the user JUST ticked it
        complete_task(t["id"])
        st.rerun()
    title = f"~~{t['title']}~~" if t["done"] else f"**{t['title']}**"
    details = f"due: {t['due'] or 'no date'}"
    if t.get("minutes"):
        details += f" · {t['minutes']} min"
    if t.get("goal_id") in goal_names:
        details += f" · 🎯 {goal_names[t['goal_id']]}"
    if governance.find_injection(t["title"]):
        details += " · :orange[🛡️ hidden from AI: looks like instructions]"
    c2.markdown(f"{ICONS[t['priority']]} {title}  \n:gray[{details}]")
    if c3.button("🗑️", key=f"{key_prefix}_del_{t['id']}", help="Delete"):
        delete_task(t["id"])
        st.rerun()


tasks_tab, goals_tab, review_tab, costs_tab, gov_tab = st.tabs(
    ["📋 Tasks & chat", "🎯 Goals", "📊 Weekly review", "💰 Costs & accuracy", "🛡️ Governance"]
)

# ======================= Tab 1: tasks and chat =======================
with tasks_tab:
    left, right = st.columns([1, 1], gap="large")

    with left:
        st.subheader("My tasks")
        with st.form("add_form", clear_on_submit=True):
            title = st.text_input("New task")
            col1, col2 = st.columns(2)
            due = col1.date_input("Due date", value=None)
            priority = col2.selectbox("Priority", ["medium", "high", "low"])
            if st.form_submit_button("Add task") and title.strip():
                add_task(title.strip(), due.isoformat() if due else None, priority)
                st.rerun()

        col_a, col_b = st.columns(2)
        show_done = col_a.toggle("Show finished tasks")
        only_soon = col_b.toggle("Only next 7 days", value=True, help="Goal plans add many future tasks")
        tasks = list_tasks(show_done)
        if only_soon:
            limit = (date.today() + timedelta(days=7)).isoformat()
            tasks = [t for t in tasks if not t["due"] or t["due"] <= limit]
        order = {"high": 0, "medium": 1, "low": 2}
        tasks.sort(key=lambda t: (t["done"], t["due"] or "9999", order[t["priority"]]))

        if not tasks:
            st.info("No tasks here. Add one above, ask the AI on the right, or create a goal.")
        for t in tasks:
            show_task_row(t, "list")

    with right:
        st.subheader("Chat with the AI agent")
        st.caption('Try: "Add gym tomorrow and study AWS on Friday, it\'s urgent" or "Plan my day"')

        box = st.container(height=450)
        for who, text in st.session_state.messages:
            box.chat_message(who).markdown(text)

        # Human approval: risky actions the AI asked for, waiting for a human decision
        for i, item in enumerate(list(st.session_state.pending)):
            with st.container(border=True):
                st.markdown(f"⚠️ **The AI wants to:** {item['description']}")
                c1, c2 = st.columns(2)
                if c1.button("✅ Approve", key=f"approve_{i}"):
                    approve(item)
                    st.session_state.pending.remove(item)
                    st.session_state.messages.append(("assistant", f":green[Approved: {item['description']}]"))
                    st.rerun()
                if c2.button("❌ Reject", key=f"reject_{i}"):
                    reject(item)
                    st.session_state.pending.remove(item)
                    st.session_state.messages.append(("assistant", f":red[Rejected: {item['description']}]"))
                    st.rerun()

        message = st.chat_input("Tell the agent what to do...")
        if message:
            st.session_state.messages.append(("user", message))
            tools_used = []
            with st.spinner("Thinking..."):
                try:
                    reply = chat(message, st.session_state.history, st.session_state.pending,
                                 on_tool=lambda name, tool_input: tools_used.append(name))
                except Exception as error:
                    reply = f"AI error: {error}"
            if tools_used:
                reply += "\n\n:gray[🔧 tools used: " + ", ".join(tools_used) + "]"
            st.session_state.messages.append(("assistant", reply))
            st.rerun()

        if st.button("Clear chat"):
            for item in st.session_state.pending:
                reject(item)  # Nothing risky is left waiting when the chat is cleared
            st.session_state.history = []
            st.session_state.messages = []
            st.session_state.pending = []
            st.rerun()

# ======================= Tab 2: goals =======================
with goals_tab:
    st.subheader("Turn a big goal into a daily plan")
    st.caption("The AI breaks your goal into steps. Python fits them into your free days. Miss a day? Re-plan.")

    with st.form("goal_form"):
        goal_title = st.text_input("Your goal", placeholder="Pass the AWS Generative AI certification")
        c1, c2 = st.columns(2)
        deadline = c1.date_input("Deadline", value=date.today() + timedelta(days=30), min_value=date.today() + timedelta(days=1))
        minutes = c2.number_input("Minutes per day", min_value=15, max_value=480, value=60, step=15)
        days = st.multiselect("Days you can work on it", WEEKDAYS, default=WEEKDAYS[:5])
        if st.form_submit_button("✨ Make my plan") and goal_title.strip():
            with st.spinner("The AI is planning your steps..."):
                try:
                    goal, new_tasks, overflow = create_goal(
                        goal_title.strip(), deadline.isoformat(), int(minutes), [WEEKDAYS.index(d) for d in days]
                    )
                    st.session_state.goal_message = ("success", f"Planned {len(new_tasks)} tasks for '{goal['title']}'.")
                    if overflow:
                        st.session_state.goal_message = ("warning", f"{overflow} steps did not fit before the deadline. "
                                                         "They are on the last day. Add more minutes or days.")
                except Exception as error:
                    st.session_state.goal_message = ("error", f"Could not make the plan: {error}")
            st.rerun()

    if "goal_message" in st.session_state:
        kind, text = st.session_state.pop("goal_message")
        getattr(st, kind)(text)  # st.success / st.warning / st.error

    for g in list_goals():
        p = goal_progress(g["id"])
        with st.container(border=True):
            st.markdown(f"### 🎯 {g['title']}")
            day_names = ", ".join(WEEKDAYS[d][:3] for d in g["study_days"])
            st.caption(f"Deadline {g['deadline']} · {g['minutes_per_day']} min/day · {day_names}")
            st.progress(p["percent"] / 100, text=f"{p['percent']}% done ({p['tasks_done']}/{p['tasks_total']} tasks)")

            c1, c2 = st.columns(2)
            if p["missed"]:
                c1.warning(f"You missed {p['missed']} task(s).")
                if c1.button("🔁 Re-plan the rest", key=f"replan_{g['id']}"):
                    moved, overflow = replan_goal(g["id"])
                    st.session_state.goal_message = ("success", f"Moved {moved} task(s) to new days.")
                    if overflow:
                        st.session_state.goal_message = ("warning", f"Moved {moved} task(s), but {overflow} no longer "
                                                         "fit before the deadline.")
                    st.rerun()
            else:
                c1.success("On track!")
            if c2.button("Delete goal and its tasks", key=f"delgoal_{g['id']}"):
                delete_goal(g["id"])
                st.rerun()

            with st.expander("See the plan"):
                for t in sorted(goal_tasks(g["id"]), key=lambda t: (t["due"] or "", t["id"])):
                    show_task_row(t, f"goal{g['id']}")

# ======================= Tab 3: weekly review =======================
with review_tab:
    st.subheader("Your last 7 days")
    stats = week_stats()
    c1, c2, c3 = st.columns(3)
    c1.metric("Tasks due", stats["tasks_due"])
    c2.metric("Done", stats["tasks_done"])
    c3.metric("Done %", f"{stats['done_percent']}%")

    if stats["per_weekday"]:
        st.markdown("**Done vs missed per weekday**")
        rows = [{"day": day[:3], "result": result, "tasks": counts[result]}
                for day, counts in stats["per_weekday"].items() for result in ("done", "missed")]
        chart = alt.Chart(alt.Data(values=rows)).mark_bar().encode(
            x=alt.X("day:N", sort=[d[:3] for d in WEEKDAYS], title=None,  # Monday first, not A-Z
                    axis=alt.Axis(labelAngle=0)),
            y=alt.Y("tasks:Q", title="tasks", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("result:N", scale=alt.Scale(domain=["done", "missed"], range=["#2e9e5b", "#d9534f"])),
        )
        st.altair_chart(chart, use_container_width=True)

    st.caption("Python counts the numbers above. The AI only writes advice from them.")
    if st.button("🧠 Write my weekly review"):
        with st.spinner("Reviewing your week..."):
            try:
                _, st.session_state.review = weekly_review()
            except Exception as error:
                st.session_state.review = f"AI error: {error}"
    if "review" in st.session_state:
        st.info(st.session_state.review)

# ======================= Tab 4: costs and accuracy =======================
with costs_tab:
    st.subheader("💰 What the AI costs")
    s = cost_summary()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total spent", f"${s['total_usd']:.4f}")
    c2.metric("Today", f"${s['today_usd']:.4f}")
    c3.metric("AI calls", s["calls"])
    c4.metric("Tokens", f"{s['tokens']:,}")
    if s["by_feature"]:
        st.markdown("**Cost per feature**")
        st.dataframe(
            [{"feature": name, "calls": f["calls"], "cost (USD)": round(f["cost_usd"], 5)}
             for name, f in sorted(s["by_feature"].items(), key=lambda x: -x[1]["cost_usd"])],
            hide_index=True,
        )

    st.subheader("🎯 How accurate is the AI?")
    report_file = Path(__file__).parent / "evals" / "last_report.json"
    if report_file.exists():
        report = json.loads(report_file.read_text(encoding="utf-8"))
        st.metric("Last accuracy test", f"{report['score_percent']}%", help=f"Run at {report['run_at']}")
        st.caption(f"{report['passed']} of {report['total']} test sentences fully correct (dates and priorities).")
        failed = [r for r in report["results"] if not r["passed"]]
        if failed:
            with st.expander(f"See the {len(failed)} failure(s)"):
                for r in failed:
                    st.markdown(f"**{r['sentence']}**")
                    for problem in r["problems"]:
                        st.markdown(f"- {problem}")
    else:
        st.info("No accuracy test yet. Run:  .venv\\Scripts\\python.exe evals\\run_evals.py")

# ======================= Tab 5: governance =======================
with gov_tab:
    st.subheader("🛡️ How this AI is kept under control")
    st.markdown(
        "- **Human approval:** the AI can only *ask* to delete; you decide.\n"
        "- **Limited tools:** the AI can only use 5 tools. It can't touch files, the internet or anything else.\n"
        "- **Injection filter:** task titles that look like orders to the AI are hidden from it.\n"
        "- **Daily budget:** AI features pause when today's spending reaches the limit.\n"
        "- **Audit log:** every AI action and every approval is recorded below.\n"
        "- **Accuracy tests:** see the Costs & accuracy tab. Full details: `GOVERNANCE.md`."
    )

    spent = cost_summary()["today_usd"]
    limit = governance.DAILY_BUDGET_USD
    st.progress(min(spent / limit, 1.0), text=f"Today's AI budget: ${spent:.4f} of ${limit:.2f}")

    st.markdown("**Audit log** (newest first)")
    events = governance.load_audit()
    if events:
        st.dataframe(
            [{"time": e["time"], "event": e["event"],
              "details": json.dumps({k: v for k, v in e.items() if k not in ("time", "event")})}
             for e in events],
            hide_index=True,
        )
    else:
        st.info("No AI actions yet. Chat with the agent and they will appear here.")
