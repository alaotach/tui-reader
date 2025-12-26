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
from pdfminer.high_level import extract_text

bm_tolerance = 2

state_dir = os.path.join(os.path.expanduser("~"), ".reader_app")
if not os.path.exists(state_dir):
    os.makedirs(state_dir)
state_file = os.path.join(state_dir, "state.json")
if not os.path.exists(state_file):
    with open(state_file, 'w') as f:
        json.dump({}, f)

def save_state(file_path, data):
    with open(state_file, 'r') as f:
        state = json.load(f)
    if isinstance(data, int):
        existing = state.get(file_path, {})
        existing["scroll"] = data
        existing["timestamp"] = datetime.now().isoformat()
        state[file_path] = existing
    else:
        state[file_path] = data
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)

def load_state(file_path):
    with open(state_file, 'r') as f:
        state = json.load(f)
    return state.get(file_path, {})

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

def extract_text_from_pdf(file_path):
    text = extract_text(file_path)
    if not text:
        return []
    pages = text.split("\x0c")
    paras = []
    for i, t in enumerate(pages, start=1):
        if t.strip():
            paras.append(f"--- Page {i} ---")
            lines = t.splitlines()
            buffer = []
            for line in lines:
                stripped = line.strip()
                if stripped:
                    buffer.append(stripped)
                else:
                    if buffer:
                        paras.append(" ".join(buffer))
                        buffer = []
            if buffer:
                paras.append(" ".join(buffer))
    return paras

def extract_pdf_pages(lines):
    pages = []
    for i, line in enumerate(lines):
        if line.startswith("--- Page ") and line.endswith(" ---"):
            try:
                page_num = int(line.split("Page ")[1].split(" ---")[0])
                pages.append({
                    "page": page_num,
                    "scroll": i
                })
            except ValueError:
                pass
    return pages


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
        self.scroll = scroll['scroll'] if isinstance(scroll, dict) else scroll
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
        ("b", "bookmark", "Bookmark"),
        ("m", "show_bookmarks", "View Bookmarks"),
        ("p", "pages", "PDF Pages"),
    ]
    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.reader: Reader = None
        self.view: ReadingView = None
    def compose(self):
        yield ReadingView(id="reader-view")
    def on_mount(self):
        if self.file_path.endswith(".pdf"):
            paras = extract_text_from_pdf(self.file_path)
            if not paras:
                paras = ["[Error extracting text from PDF]"]
        else:
            paras = load_text(self.file_path)
        wlines = []
        for para in paras:
            wlines.extend(wrap_text(para, width=max_width))
            wlines.append("")
        
        self.reader = Reader(wlines, scroll=0)
        self.view = self.query_one(ReadingView)
        
        saved_state = load_state(self.file_path)
        saved_scroll = saved_state.get("scroll", 0)
        if saved_scroll > 0:
            prompt = ResumePrompt(self.file_path, saved_scroll, len(wlines))
            self.push_screen(prompt, callback=self._handle_resume_choice)
        else:
            self.update_view()

    def _handle_resume_choice(self, resume: bool | None):
        if resume:
            saved_state = load_state(self.file_path)
            self.reader.scroll = saved_state.get("scroll", 0)
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

    def action_bookmark(self):
        data = load_state(self.file_path)
        bookmarks = data.get("bookmarks", [])
        
        if self.file_path.endswith(".pdf"):
            page_num = 1
            for i in range(self.reader.scroll, -1, -1):
                line = self.reader.lines[i]
                if line.startswith("--- Page ") and line.endswith(" ---"):
                    page_num = int(line.split("Page ")[1].split(" ---")[0])
                    break
            preview = f"Page {page_num}"
        else:
            preview = self.reader.lines[self.reader.scroll][:50]

        for bm in bookmarks:
            if abs(bm["scroll"] - self.reader.scroll) <= bm_tolerance:
                bm["scroll"] = self.reader.scroll
                bm["preview"] = preview
                data["bookmarks"] = bookmarks
                data["scroll"] = self.reader.scroll
                data["timestamp"] = datetime.now().isoformat()
                save_state(self.file_path, data)
                return
        bookmarks.append({
            "scroll": self.reader.scroll,
            "preview": preview,
        })
        data["bookmarks"] = bookmarks
        data["scroll"] = self.reader.scroll
        data["timestamp"] = datetime.now().isoformat()
        save_state(self.file_path, data)
    def action_show_bookmarks(self):
        data = load_state(self.file_path)
        bookmarks = data.get("bookmarks", [])
        if not bookmarks:
            return
        self.push_screen(
            BookmarkScreen(bookmarks, self.file_path),
            callback=self._handle_toc_jump
        )

    def action_pages(self):
        if not self.file_path.endswith(".pdf"):
            return
        pages = extract_pdf_pages(self.reader.lines)
        if not pages:
            return
        self.push_screen(
            PdfPageScreen(pages),
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

class PdfPageScreen(Screen):
    CSS = """
    PdfPageScreen {
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
    .selected {
        background: #444444;
    }
    """
    def __init__(self, pages):
        super().__init__()
        self.pages = pages
        self.index = 0
        self.input_mode = False
        self.buffer = ""
    
    def compose(self):
        with Vertical(id="box"):
            yield Static("Pages (Type page number and press Enter to jump)\n")
            for i, item in enumerate(self.pages):
                yield Static(
                    f"Page {item['page']}",
                    classes="selected" if i == self.index else ""
                )
    def on_key(self, event: Key):
        if self.input_mode:
            if event.key == "enter":
                if self.buffer:
                    try:
                        page_num = int(self.buffer)
                        for p in self.pages:
                            if p["page"] == page_num:
                                self.dismiss(p["scroll"])
                                return
                    except ValueError:
                        pass
                self._exit_input_mode()
                
            elif event.key == "escape":
                self._exit_input_mode()
                
            elif event.key == "backspace":
                self.buffer = self.buffer[:-1]
                if self.buffer:
                    title = self.query_one(Static)
                    title.update(f"Pages - Enter page: {self.buffer}")
                else:
                    self._exit_input_mode()
            elif len(event.key) == 1 and event.key in "0123456789":
                self.buffer += event.key
                title = self.query_one(Static)
                title.update(f"Pages - Enter page: {self.buffer}")
            return
        if len(event.key) == 1 and event.key in "0123456789":
            self.input_mode = True
            self.buffer = event.key
            title = self.query_one(Static)
            title.update(f"Pages - Enter page: {self.buffer}")
        elif event.key == "up":
            self.index = max(0, self.index - 1)
            self._update_selection()
        elif event.key == "down":
            self.index = min(len(self.pages) - 1, self.index + 1)
            self._update_selection()
        elif event.key == "enter":
            self.dismiss(self.pages[self.index]["scroll"])
        elif event.key.lower() == "q":
            self.dismiss(None)
    def _update_selection(self):
        statics = self.query(Static)
        for i, static in enumerate(list(statics)[1:]):
            if i == self.index:
                static.add_class("selected")
            else:
                static.remove_class("selected")
    def _exit_input_mode(self):
        self.input_mode = False
        self.buffer = ""
        title = self.query_one(Static)
        title.update("Pages (Type page number and press Enter to jump)\n")

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
        self.scroll = scroll['scroll'] if isinstance(scroll, dict) else scroll
        self.total_lines = total_lines
        self.file_name = os.path.basename(file_path)
        self.progress = int((scroll['scroll'] if isinstance(scroll, dict) else scroll / max(total_lines - 1, 1)) * 100)

    def compose(self):
        yield Vertical(
            Static("Resume reading?\n"),
            Static(f"File: {self.file_name}"),
            Static(f"Progress: {self.progress}%\n"),
            Static("[R] Resume    [S] Start over    [Q] Quit"),
            id="box",
        )

    def on_key(self, event: textual.events.Key):
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
    .selected {
        background: #444444;
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
            old_index = self.index
            self.index = max(0, self.index - 1)
            if old_index != self.index:
                self._update_selection()
        elif event.key == "down":
            old_index = self.index
            self.index = min(len(self.toc) - 1, self.index + 1)
            if old_index != self.index:
                self._update_selection()
        elif event.key == "enter":
            self.dismiss(self.toc[self.index][0])
        elif event.key.lower() == "q":
            self.dismiss(None)
    
    def _update_selection(self):
        statics = self.query(Static)
        for i, static in enumerate(list(statics)[1:]):
            if i == self.index:
                static.add_class("selected")
            else:
                static.remove_class("selected")
    
class BookmarkScreen(Screen):
    CSS = """
    BookmarkScreen {
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
    .selected {
        background: #444444;
    }
    """
    def __init__(self, bookmarks, file_path):
        super().__init__()
        self.bookmarks = bookmarks
        self.file_path = file_path
        self.index = 0
    def compose(self):
        with Vertical(id="box"):
            yield Static("Bookmarks\n")
            for i, bm in enumerate(self.bookmarks):
                yield Static(
                    f"{i+1}. {bm['preview']}",
                    classes="selected" if i == self.index else ""
                )
    def on_key(self, event: Key):
        if event.key == "up":
            old_index = self.index
            self.index = max(0, self.index - 1)
            if old_index != self.index:
                self._update_selection()
        elif event.key == "down":
            old_index = self.index
            self.index = min(len(self.bookmarks) - 1, self.index + 1)
            if old_index != self.index:
                self._update_selection()
        elif event.key == "enter":
            self.dismiss(self.bookmarks[self.index]["scroll"])
        elif event.key == "delete":
            if len(self.bookmarks) > 0:
                del self.bookmarks[self.index]
                data = load_state(self.file_path)
                data["bookmarks"] = self.bookmarks
                save_state(self.file_path, data)
                if len(self.bookmarks) == 0:
                    self.dismiss(None)
                else:
                    if self.index >= len(self.bookmarks):
                        self.index = len(self.bookmarks) - 1
                    self._rebuild_list()
        elif event.key.lower() == "q":
            self.dismiss(None)
    
    def _update_selection(self):
        statics = self.query(Static)
        for i, static in enumerate(list(statics)[1:]):
            if i == self.index:
                static.add_class("selected")
            else:
                static.remove_class("selected")

    def _rebuild_list(self):
        container = self.query_one("#box")
        statics = list(container.query(Static))
        for static in statics[1:]:
            static.remove()
        for i, bm in enumerate(self.bookmarks):
            new_static = Static(
                f"{i+1}. {bm['preview']}",
                classes="selected" if i == self.index else ""
            )
            container.mount(new_static)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python main.py <text_file>")
        sys.exit(1)
    file_path = sys.argv[1]
    app = ReaderApp(file_path)
    app.run()