import textwrap
import sys
import textual
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Static

def load_text(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        c = f.read()
    paras = c.split('\n\n')
    return [p.strip() for p in paras if p.strip()]
def wrap_text(text, width=70):
    return textwrap.wrap(text, width=width, replace_whitespace=False, drop_whitespace=False)

class Reader:
    def __init__(self, lines):
        self.lines = lines
        self.scroll = 0
    def scroll_down(self, n=1):
        self.scroll = min(self.scroll + n, len(self.lines) - 1)
    def scroll_up(self, n=1):
        self.scroll = max(self.scroll - n, 0)
    def get_visible_lines(self, height):
        return self.lines[self.scroll:self.scroll + height]

max_width = 70

class ReadingView(Static):
    pass

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
        self.reader = Reader(wlines)
        self.view = self.query_one(ReadingView)
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

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python main.py <text_file>")
        sys.exit(1)
    file_path = sys.argv[1]
    app = ReaderApp(file_path)
    app.run()