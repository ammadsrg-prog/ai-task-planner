from agent import approve, chat, reject
from ai_parser import parse_tasks
from storage import add_task, list_tasks, complete_task, delete_task


def show_tasks(tasks):
    if not tasks:
        print("No tasks yet.")
        return
    for t in tasks:
        mark = "x" if t["done"] else " "
        due = t["due"] or "no date"
        print(f"[{mark}] {t['id']}. {t['title']}  (due: {due}, priority: {t['priority']})")


def ask_task_id(question):
    text = input(question).strip()
    if not text.isdigit():
        print("Please type a number.")
        return None
    return int(text)


def main():
    history = []  # The agent's memory for this session

    while True:
        print("\n--- Task Planner ---")
        print("1. Add task")
        print("2. Show tasks")
        print("3. Complete task")
        print("4. Delete task")
        print("5. Add tasks with AI")
        print("6. Chat with AI agent")
        print("0. Quit")
        choice = input("Choose 0-6: ").strip()

        if choice == "1":
            title = input("Title: ").strip()
            if not title:
                print("Title can't be empty.")
                continue
            due = input("Due date (YYYY-MM-DD, or Enter to skip): ").strip() or None
            priority = input("Priority (low/medium/high, Enter = medium): ").strip() or "medium"
            task = add_task(title, due, priority)
            print(f"Added task {task['id']}.")

        elif choice == "2":
            show_tasks(list_tasks())

        elif choice == "3":
            task_id = ask_task_id("Task id to complete: ")
            if task_id is not None:
                print("Done!" if complete_task(task_id) else "No task with that id.")

        elif choice == "4":
            task_id = ask_task_id("Task id to delete: ")
            if task_id is not None:
                print("Deleted." if delete_task(task_id) else "No task with that id.")

        elif choice == "5":
            sentence = input("Describe your tasks: ").strip()
            if not sentence:
                continue
            print("Asking the AI...")
            try:
                new_tasks = parse_tasks(sentence)
            except Exception as error:
                print(f"AI error: {error}")
                continue
            for t in new_tasks:
                task = add_task(t["title"], t["due"], t["priority"])
                print(f"Added task {task['id']}: {task['title']} (due: {task['due'] or 'no date'}, {task['priority']})")

        elif choice == "6":
            print("Chat with the agent. Type 'back' to return to the menu.")
            while True:
                message = input("You: ").strip()
                if message.lower() in ("back", ""):
                    break
                pending = []
                try:
                    print("AI:", chat(message, history, pending))
                except Exception as error:
                    print(f"AI error: {error}")
                # Human approval: the AI can only ask to delete, the user decides
                for item in pending:
                    answer = input(f"  ⚠️  The AI wants to: {item['description']}. Approve? (y/n): ")
                    if answer.strip().lower() == "y":
                        approve(item)
                        print("  Approved and done.")
                    else:
                        reject(item)
                        print("  Rejected. Nothing was deleted.")

        elif choice == "0":
            print("Bye!")
            break

        else:
            print("Please choose a number from 0 to 6.")


if __name__ == "__main__":
    main()
