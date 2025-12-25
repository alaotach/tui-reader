import textwrap
import sys
import textual
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Static
from textual.widget import Widget
from textual.reactive import reactive
from textual.message import Message
import textual.events
from textual.events import Key
from textual.screen import Screen
import json
import os
from datetime import datetime

state_dir = os.path.join(os.path.expanduser("~"), ".reader_app")
if not os.path.exists(state_dir):
    os.makedirs(state_dir)
state_file = os.path.join(state_dir, "state.json")
if not os.path.exists(state_file):
    with open(state_file, 'w') as f:
        json.dump({}, f)

def save_state(file_path, scroll):
    with open(state_file, 'r') as f:
        state = json.load(f)
    state[file_path] = {
        "scroll": scroll,
        "timestamp": datetime.now().isoformat()
    }
    with open(state_file, 'w') as f:
        json.dump(state, f)

def load_state(file_path):
    with open(state_file, 'r') as f:
        state = json.load(f)
    if file_path in state:
        return state[file_path]["scroll"]
    return 0

def parse_toc(lines):
    toc = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level:].strip()
            if title:
                toc.append((i, level, title))
    return toc

def load_text(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        c = f.read()
    paras = c.split('\n\n')
    return [p.strip() for p in paras if p.strip()]
def wrap_text(text, width=70):
    return textwrap.wrap(text, width=width, replace_whitespace=False, drop_whitespace=False)

class Reader:
    def __init__(self, lines, scroll=0):
        self.lines = lines
        self.scroll = scroll
    def scroll_down(self, n=1):
        self.scroll = min(self.scroll + n, len(self.lines) - 1)
    def scroll_up(self, n=1):
        self.scroll = max(self.scroll - n, 0)
    def get_visible_lines(self, height):
        return self.lines[self.scroll:self.scroll + height]

max_width = 70

class ReadingView(Static):
    pass

class ResumeDecision(Message):
    def __init__(self, resume: bool, scroll: int):
        self.resume = resume
        self.scroll = scroll
        super().__init__()

class ReaderApp(App):
    CSS = """
    ReaderApp {
        background: black;
        color: white;
        padding: 1;
        }
    """
    BINDINGS = [
        ("j", "scroll_down", "Scroll Down"),
        ("k", "scroll_up", "Scroll Up"),
        ("q", "quit", "Quit"),
        ("t", "toc", "Table of Contents"),
    ]
    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.reader: Reader = None
        self.view: ReadingView = None
    def compose(self) -> ComposeResult:
        yield ReadingView(id="reader-view")
    def on_mount(self):
        paras = load_text(self.file_path)
        wlines = []
        for para in paras:
            wlines.extend(wrap_text(para, width=max_width))
            wlines.append("")
        
        self.reader = Reader(wlines, scroll=0)
        self.view = self.query_one(ReadingView)
        
        saved_scroll = load_state(self.file_path)
        if saved_scroll > 0:
            prompt = ResumePrompt(self.file_path, saved_scroll, len(wlines))
            self.push_screen(prompt, callback=self._handle_resume_choice)
        else:
            self.update_view()

    def _handle_resume_choice(self, resume: bool | None):
        if resume:
            self.reader.scroll = load_state(self.file_path)
        else:
            self.reader.scroll = 0
        self.update_view()
    def update_view(self):
        if self.reader and self.view:
            height = self.size.height - 2
            visible_lines = self.reader.get_visible_lines(height)
            self.view.update("\n".join(visible_lines))
    def action_scroll_down(self):
        if self.reader:
            self.reader.scroll_down()
            self.update_view()

    def action_scroll_up(self):
        if self.reader:
            self.reader.scroll_up()
            self.update_view()
    def action_toc(self):
        if not self.file_path.endswith(".md"):
            return

        toc = parse_toc(self.reader.lines)
        if not toc:
            return

        self.push_screen(
            TocScreen(toc),
            callback=self._handle_toc_jump
        )

    def action_quit(self):
        if self.reader:
            save_state(self.file_path, self.reader.scroll)
        self.exit()
    
    def on_resume_decision(self, message: ResumeDecision):
        if resume is True:
            self.reader.scroll = load_state(self.file_path)
            self.update_view()

        elif resume is False:
            self.reader.scroll = 0
            self.update_view()

        else:
            saved_scroll = load_state(self.file_path)
            save_state(self.file_path, saved_scroll)
            self.exit()
    def _handle_toc_jump(self, line: int | None):
        if line is not None:
            self.reader.scroll = line
            self.update_view()

class ResumePrompt(Screen):
    CSS = """
    ResumePrompt {
        background: black;
        align: center middle;
    }

    #box {
        width: 50;
        padding: 1 2;
        border: round white;
    }
    """

    def __init__(self, file_path: str, scroll: int, total_lines: int):
        super().__init__()
        self.file_path = file_path
        self.scroll = scroll
        self.total_lines = total_lines
        self.file_name = os.path.basename(file_path)
        self.progress = int((scroll / max(total_lines - 1, 1)) * 100)

    def compose(self):
        yield Vertical(
            Static("Resume reading?\n"),
            Static(f"File: {self.file_name}"),
            Static(f"Progress: {self.progress}%\n"),
            Static("[R] Resume    [S] Start over    [Q] Quit"),
            id="box",
        )

    def on_key(self, event: textual.events.Key) -> None:
        if event.key.lower() == "r":
            self.dismiss(True)
        elif event.key.lower() == "s":
            self.dismiss(False)
        elif event.key.lower() == "q":
            self.dismiss(None)

class TocScreen(Screen):
    CSS = """
    TocScreen {
        background: black;
        align: center middle;
    }
    #box {
        width: 60;
        height: 80%;
        padding: 1 2;
        border: round white;
        overflow: auto;
    }
    """
    def __init__(self, toc):
        super().__init__()
        self.toc = toc
        self.index = 0
    def compose(self):
        with Vertical(id="box"):
            yield Static("Table of Contents\n")
            for i, item in enumerate(self.toc):
                indent = "  " * (item[1] - 1)
                yield Static(
                    f"{indent}{item[2]}",
                    classes="selected" if i == self.index else ""
                )
    def on_key(self, event: Key):
        if event.key == "up":
            self.index = max(0, self.index - 1)
            self.refresh()
        elif event.key == "down":
            self.index = min(len(self.toc) - 1, self.index + 1)
            self.refresh()
        elif event.key == "enter":
            self.dismiss(self.toc[self.index][0])
        elif event.key.lower() == "q":
            self.dismiss(None)
    

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python main.py <text_file>")
        sys.exit(1)
    file_path = sys.argv[1]
    app = ReaderApp(file_path)
    app.run()