"""
Entrypoint for the Agentic AI system.

Usage:
  python main.py              # launch Gradio UI (default)
  python main.py --cli        # interactive terminal chat
  python main.py --query "…"  # single-shot query, prints result
"""
from dotenv import load_dotenv
load_dotenv()  # load .env before any other import

import argparse
import sys
from loguru import logger


def launch_ui():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "chase"))
    from telegram_bot import start_bot_background
    from notifier import start_notifier_daemon
    start_bot_background()
    start_notifier_daemon()

    from ui.app import build_ui
    import gradio as gr
    app = build_ui()
    root_path = os.getenv("GRADIO_ROOT_PATH", "")
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        root_path=root_path,
        theme=gr.themes.Soft(
            primary_hue="blue",
            neutral_hue="slate",
            radius_size="sm",
            spacing_size="md",
        ),
    )


def cli_chat():
    from graph.workflow import build_graph, run_query
    logger.info("Building graph…")
    graph = build_graph()
    thread_id = "cli-session"
    print("\n🤖 Agentic AI — CLI mode (type 'exit' to quit)\n")
    while True:
        try:
            query = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break
        if not query or query.lower() in ("exit", "quit"):
            break
        response = run_query(graph, query, thread_id=thread_id)
        print(f"\nAgent: {response}\n")


def single_query(query: str):
    from graph.workflow import build_graph, run_query
    graph = build_graph()
    result = run_query(graph, query)
    print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agentic AI")
    parser.add_argument("--cli", action="store_true", help="Interactive CLI mode")
    parser.add_argument("--query", type=str, help="Single query mode")
    args = parser.parse_args()

    if args.query:
        single_query(args.query)
    elif args.cli:
        cli_chat()
    else:
        launch_ui()
