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
from textual.events import Paste
import json
import os
from datetime import datetime
from pdfminer.high_level import extract_text
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from io import StringIO
from pdfminer.pdfpage import PDFPage
import time
import asyncio
import subprocess
try:
    from vosk import Model, KaldiRecognizer
    import pyaudio
    import queue
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False

exts = ['.txt', '.md', '.pdf']

THEMES = {
    "dark": {
        "name" : "dark",
        "background": "black",
        "text": "#d0d0d0",
    },
    "paper": {
        "name" : "paper",
        "background": "#fdf6e3",
        "text": "#3a3a3a",
    },
    "sepia": {
        "name" : "sepia",
        "background": "#f4ecd8",
        "text": "#5b4636",
    }
}

bm_tolerance = 2

state_dir = os.path.join(os.path.expanduser("~"), ".reader_app")
cache_dir = os.path.join(state_dir, "cache")
os.makedirs(cache_dir, exist_ok=True)

def cache_path(file_path):
    safe = (os.path.abspath(file_path).replace("\\", "_").replace("/", "_").replace(":", ""))
    return os.path.join(cache_dir, safe + ".json")

if not os.path.exists(state_dir):
    os.makedirs(state_dir)
state_file = os.path.join(state_dir, "state.json")
if not os.path.exists(state_file):
    with open(state_file, 'w') as f:
        json.dump({}, f)

def scan_folder(folder_path):
    files = []
    for root, dirs, filenames in os.walk(folder_path):
        for filename in filenames:
            if any(filename.lower().endswith(ext) for ext in exts):
                files.append(os.path.join(root, filename))
    return files


def build_library():
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []
    
    library = []
    for path, data in state.items():
        if path.startswith("_"):
            continue
        if not os.path.exists(path):
            continue
        scroll = data.get("scroll", 0)
        total = data.get("total_lines", 1)
        
        if total is None or total <= 1:
            progress = 0
        else:
            progress = min(100, int((scroll / max(total - 1, 1)) * 100))
        library.append({
            "path": path,
            "scroll": scroll,
            "total_lines": total,
            "progress": progress,
            "timestamp": data.get("timestamp", "")
        })
    library.sort(key=lambda x: x["timestamp"], reverse=True)
    
    return library

def load_theme():
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        with open(state_file, 'w', encoding="utf-8") as f:
            json.dump({}, f)
        return THEMES["dark"]

    theme_state = state.get("_theme", {})
    theme_name = theme_state.get("theme", "dark")

    return THEMES.get(theme_name, THEMES["dark"])


def save_theme(theme_name):
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        state = {}

    if "_theme" not in state:
        state["_theme"] = {}

    state["_theme"]["theme"] = theme_name

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def save_state(file_path, data):
    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        state = {}
    
    if isinstance(data, int):
        existing = state.get(file_path, {})
        existing["scroll"] = data
        existing["timestamp"] = datetime.now().isoformat()
        state[file_path] = existing
    else:
        state[file_path] = data
    
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)

def load_state(file_path):
    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
        return state.get(file_path, {})
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

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

def load_or_parse(file_path):
    if file_path.endswith(".pdf"):
        return LazyPdfLoader(file_path)
    else:
        cpath = cache_path(file_path)
        mtime = os.path.getmtime(file_path)
        if os.path.exists(cpath):
            with open(cpath, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("mtime") == mtime and "paras" in cached:
                return cached["paras"]
        paras = load_text(file_path)
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"mtime": mtime, "paras": paras}, f)
        return paras

class LazyPdfLoader:
    def __init__(self, file_path):
        self.file_path = file_path
        self.cpath = cache_path(file_path)
        self.mtime = os.path.getmtime(file_path)
        self.cache_data = self._load_cache()
        self.total_pages = self._count_pages()
        self.page_para_map = {}
        self.current_max_para = 0
    def _load_cache(self):
        if os.path.exists(self.cpath):
            with open(self.cpath, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("mtime") == self.mtime and "pages" in cached:
                return cached
        return {"mtime": self.mtime, "pages": {}, "total_pages": None}
    def _count_pages(self):
        if self.cache_data.get("total_pages"):
            return self.cache_data["total_pages"]
        with open(self.file_path, 'rb') as fp:
            total = len(list(PDFPage.get_pages(fp)))
        self.cache_data["total_pages"] = total
        self._save_cache()
        return total
    
    def _save_cache(self):
        with open(self.cpath, "w", encoding="utf-8") as f:
            json.dump(self.cache_data, f)
    
    def _extract_page(self, page_num):
        with open(self.file_path, 'rb') as fp:
            rmgr = PDFResourceManager()
            pages_iter = PDFPage.get_pages(fp)
            
            for i, page in enumerate(pages_iter, start=1):
                if i == page_num:
                    output = StringIO()
                    device = TextConverter(rmgr, output, laparams=LAParams())
                    ctx = PDFPageInterpreter(rmgr, device)
                    ctx.process_page(page)
                    text = output.getvalue()
                    device.close()
                    output.close()
                    paras = []
                    if text.strip():
                        paras.append(f"--- Page {page_num} ---")
                        lines = text.splitlines()
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
        return []
    
    def get_page(self, page_num):
        page_key = str(page_num)
        if page_key not in self.cache_data["pages"]:
            paras = self._extract_page(page_num)
            self.cache_data["pages"][page_key] = paras
            self._save_cache()
        return self.cache_data["pages"][page_key]
    
    def get_para(self, index):
        for page_num in range(1, self.total_pages + 1):
            if page_num not in self.page_para_map:
                paras = self.get_page(page_num)
                start = self.current_max_para
                end = start+len(paras)
                self.page_para_map[page_num] = (start, end)
                self.current_max_para = end
            start, end = self.page_para_map[page_num]
            if start <= index < end:
                paras = self.get_page(page_num)
                local_idx = index - start
                if local_idx < len(paras):
                    return paras[local_idx]
                return ""
        return ""

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
            except:
                pass
    return pages


class Reader:
    def __init__(self, paras, width):
        self.paras = paras
        self.width = width
        self.scroll = 0
        self.line_cache = {}
        self.is_lazy_pdf = isinstance(paras, LazyPdfLoader)
        self.para_offsets = self._build_offsets()
    
    def _build_offsets(self):
        offsets = []
        current = 0
        para_count = len(self.paras) if not self.is_lazy_pdf else self.paras.total_pages * 10
        
        if self.is_lazy_pdf:
            self.total_lines = None
            return offsets
        
        for i in range(para_count):
            if self.is_lazy_pdf:
                p = self.paras[i]
            else:
                if i >= len(self.paras):
                    break
                p = self.paras[i]
            wrapped = max(1, len(textwrap.wrap(p, self.width)))
            offsets.append(current)
            current += wrapped + 1
        self.total_lines = current
        return offsets
    
    def _get_para(self, index):
        if self.is_lazy_pdf:
            if not hasattr(self, '_pcache'):
                self._pcache = {}
                self._poffs = {}
                self._cline = 0
                self._cpara = 0
            while self._cline <= index:
                if self._cpara not in self._pcache:
                    try:
                        para = self.paras.get_para(self._cpara)
                        if not para:
                            if self.total_lines is None:
                                self.total_lines = self._cline
                            break
                        self._pcache[self._cpara] = para
                        self._poffs[self._cpara] = self._cline
                        wrapped_count = max(1, len(textwrap.wrap(para, self.width)))
                        self._cline += wrapped_count + 1
                        self._cpara += 1
                    except:
                        if self.total_lines is None:
                            self.total_lines = self._cline
                        break
                else:
                    para = self._pcache[self._cpara]
                    wrapped_count = max(1, len(textwrap.wrap(para, self.width)))
                    self._cline += wrapped_count + 1
                    self._cpara += 1
            para_i = max((i for i, off in self._poffs.items() if off <= index), default=0)
            return para_i, self._pcache.get(para_i, ""), self._poffs.get(para_i, 0)
        else:
            para_i = max((i for i, off in enumerate(self.para_offsets) if off <= index), default=0)
            return para_i, self.paras[para_i], self.para_offsets[para_i]
    
    def _line_from_index(self, index):
        if index in self.line_cache:
            return self.line_cache[index]
        
        para_i, para_text, start = self._get_para(index)
        local = index - start
        wrapped = textwrap.wrap(para_text, self.width) if para_text else []
        
        if local < len(wrapped):
            line = wrapped[local]
        else:
            line = ""
        
        self.line_cache[index] = line
        return line
    
    def get_visible_lines(self, height):
        lines = []
        if self.total_lines is not None:
            end = min(self.scroll + height, self.total_lines)
        else:
            end = self.scroll + height
        
        for i in range(self.scroll, end):
            try:
                lines.append(self._line_from_index(i))
            except:
                break
        return lines

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
        ("v", "toggle_voice", "Voice Control"),
        # ("l", "library", "Open Library"),
        ("T", "toggle_theme", "Toggle Theme"),
        ("ctrl+t", "theme_selector", "Select Theme"),
        ("ctrl+c", "quit", "Quit"),
    ]
    def __init__(self, file_path: str | None = None):
        super().__init__()
        self.file_path = file_path
        self.reader: Reader = None
        self.view: ReadingView = None
        self._scroll_speed = 1
        self._last_scroll_time = 0
        self._voice_active = False
        self._voice_worker = None
        self._auto_scroll_active = False
        self._auto_scroll_speed = 1
    def compose(self):
        yield ReadingView(id="reader-view")
    def apply_theme(self):
        theme = getattr(self, "_current_theme", None)
        if theme:
            self.styles.background = theme["background"]
            self.styles.color = theme["text"]
            if self.view:
                self.view.styles.background = theme["background"]
                self.view.styles.color = theme["text"]

    def on_mount(self):
        self._current_theme = load_theme()
        self.view = self.query_one(ReadingView)
        self.apply_theme()
        if not self.file_path:
            self.action_library()
            return
        
        saved_state = load_state(self.file_path)
        saved_scroll = saved_state.get("scroll", 0)
        
        if saved_scroll > 0:
            total_lines = saved_state.get("total_lines", 1)
            self.push_screen(
                ResumePrompt(self.file_path, saved_scroll, total_lines),
                callback=self._handle_resume_choice
            )
        else:
            self._load_file_internal(0)

    def _handle_resume_choice(self, resume: bool | None):
        if resume:
            saved_state = load_state(self.file_path)
            start_scroll = saved_state.get("scroll", 0)
        else:
            start_scroll = 0
        self._load_file_internal(start_scroll)
    def update_view(self):
        if self.reader and self.view:
            height = self.size.height - 2
            visible_lines = self.reader.get_visible_lines(height)
            content = "\n".join(visible_lines)
            if self._voice_active:
                indicator = "[🎙 VOICE]" if self._auto_scroll_active else "[🎙 voice]"
                content = f"{indicator}\n{content}"
            self.view.update(content)
    def action_scroll_down(self):
        if not self.reader:
            return
        t = time.time()
        if t - self._last_scroll_time < 0.3:
            self._scroll_speed = min(self._scroll_speed + 1, 10)
        else:
            self._scroll_speed = 1
        self._last_scroll_time = t
        
        if self.reader.total_lines is not None:
            self.reader.scroll = min(
                self.reader.scroll + self._scroll_speed,
                self.reader.total_lines - 1
            )
        else:
            self.reader.scroll += self._scroll_speed
        self.update_view()
        self._save_scroll_position()

    def action_scroll_up(self):
        if not self.reader:
            return
        t = time.time()
        if t - self._last_scroll_time < 0.3:
            self._scroll_speed = min(self._scroll_speed + 1, 10)
        else:
            self._scroll_speed = 1
        self._last_scroll_time = t
        
        self.reader.scroll = max(self.reader.scroll - self._scroll_speed, 0)
        self.update_view()
        self._save_scroll_position()

    def _save_scroll_position(self):
        if not self.file_path or not self.reader:
            return
        if not hasattr(self, '_scroll_count'):
            self._scroll_count = 0
        self._scroll_count += 1
        if self._scroll_count >= 10:
            self._scroll_count = 0
            state = load_state(self.file_path)
            state["scroll"] = self.reader.scroll
            state["total_lines"] = self.reader.total_lines
            state["timestamp"] = datetime.now().isoformat()
            save_state(self.file_path, state)

    def action_toc(self):
        if not self.file_path or not self.file_path.endswith(".md"):
            return
        if self.reader.total_lines is None:
            return
        all_lines = [self.reader._line_from_index(i) for i in range(self.reader.total_lines)]
        toc = parse_toc(all_lines)
        if not toc:
            return

        self.push_screen(
            TocScreen(toc),
            callback=self._handle_toc_jump
        )

    def action_bookmark(self):
        if not self.file_path:
            return
        data = load_state(self.file_path)
        bookmarks = data.get("bookmarks", [])
        
        if self.file_path.endswith(".pdf"):
            page_num = 1
            for i in range(self.reader.scroll, -1, -1):
                line = self.reader._line_from_index(i)
                if line.startswith("--- Page ") and line.endswith(" ---"):
                    page_num = int(line.split("Page ")[1].split(" ---")[0])
                    break
            preview = f"Page {page_num}"
        else:
            preview = self.reader._line_from_index(self.reader.scroll)[:50]

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
        if not self.file_path:
            return
        data = load_state(self.file_path)
        bookmarks = data.get("bookmarks", [])
        if not bookmarks:
            return
        self.push_screen(
            BookmarkScreen(bookmarks, self.file_path),
            callback=self._handle_toc_jump
        )

    def action_pages(self):
        if not self.file_path or not self.file_path.endswith(".pdf"):
            return
        
        # For PDFs, extract page markers from visible content
        if isinstance(self.reader.paras, LazyPdfLoader):
            pages = []
            page_num = 1
            scroll_pos = 0
            # Scan through loaded pages to find page markers
            for para_idx in range(self.reader.paras.total_pages * 10):
                try:
                    line = self.reader._line_from_index(scroll_pos)
                    if line.startswith("--- Page ") and line.endswith(" ---"):
                        try:
                            page_num = int(line.split("Page ")[1].split(" ---")[0])
                            pages.append({"page": page_num, "scroll": scroll_pos})
                        except:
                            pass
                    scroll_pos += 1
                except:
                    break
            
            if not pages:
                return
            self.push_screen(
                PdfPageScreen(pages),
                callback=self._handle_toc_jump
            )
        else:
            # Non-PDF shouldn't reach here but handle gracefully
            return
    
    def action_toggle_theme(self):
        current_name = self._current_theme["name"]
        theme_names = list(THEMES.keys())
        next_index = (theme_names.index(current_name) + 1) % len(theme_names)
        next_theme_name = theme_names[next_index]
        self._current_theme = THEMES[next_theme_name]
        save_theme(next_theme_name)
        self.apply_theme()
    def action_theme_selector(self):
        self.push_screen(
            ThemeSelector(),
            callback=self._handle_theme_selection
        )
    
    def action_toggle_voice(self):
        if not VOICE_AVAILABLE:
            self.push_screen(
                VoiceInstallScreen(),
                callback=self._handle_voice_install
            )
            return
        if not self.reader:
            return
        if self._voice_active:
            self._voice_active = False
            self._auto_scroll_active = False
            self.update_view()
        else:
            model_path = os.path.join(state_dir, "vosk-model")
            if not os.path.exists(model_path):
                self.push_screen(VoiceSetupScreen(model_missing=True))
                return
            self._voice_active = True
            self._voice_worker = self.run_worker(self._voice_listener(), exclusive=False)
            self.update_view()
    
    def _handle_voice_install(self, result):
        if result:
            pass
    
    async def _voice_listener(self):
        try:
            model_path = os.path.join(state_dir, "vosk-model")
            if not os.path.exists(model_path):
                return            
            model = Model(model_path)
            rec = KaldiRecognizer(model, 16000)
            rec.SetWords(True)
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16,
                          channels=1,
                          rate=16000,
                          input=True,
                          frames_per_buffer=4000)
            stream.start_stream()
            while self._voice_active:
                data = stream.read(4000, exception_on_overflow=False)
                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").lower()
                    if text:
                        self._process_voice_command(text)
                await asyncio.sleep(0.01)
            stream.stop_stream()
            stream.close()
            p.terminate()
        except:
            self._voice_active = False
            self.update_view()
    
    def _process_voice_command(self, text: str):
        if "scroll" in text and "stop" not in text:
            if not self._auto_scroll_active:
                self._auto_scroll_active = True
                self.run_worker(self._auto_scroll(), exclusive=False)
        elif "stop" in text:
            self._auto_scroll_active = False
        elif "faster" in text:
            self._auto_scroll_speed = min(self._auto_scroll_speed + 1, 15)
        elif "slower" in text or "slow" in text:
            self._auto_scroll_speed = max(self._auto_scroll_speed - 1, 1)
        elif "up" in text:
            self.action_scroll_up()
        elif "down" in text:
            self.action_scroll_down()
        self.update_view()
    
    async def _auto_scroll(self):
        while self._auto_scroll_active and self.reader:
            if self.reader.total_lines is not None:
                self.reader.scroll = min(
                    self.reader.scroll + self._auto_scroll_speed,
                    self.reader.total_lines - 1
                )
            else:
                self.reader.scroll += self._auto_scroll_speed
            self.update_view()
            await asyncio.sleep(0.1)
    def action_library(self):
        if self.reader and self.file_path:
            state = load_state(self.file_path)
            state["scroll"] = self.reader.scroll
            state["total_lines"] = self.reader.total_lines
            state["timestamp"] = datetime.now().isoformat()
            save_state(self.file_path, state)
        
        library = build_library()
        self.push_screen(
            LibraryScreen(library),
            callback=self._handle_library_selection
        )
    def _handle_library_selection(self, result):
        if result is None:
            if not self.file_path:
                self.exit()
            return
        if isinstance(result, tuple) and result[0] in ("file", "folder"):
            mode, path = result
            path = path.strip().strip('"').strip("'").strip("'").strip("'")
            path = os.path.abspath(os.path.expanduser(path))
            path = path.replace("'", "'").replace("'", "'")
            error_msg = None
            success_count = 0
            if mode == "file":
                if not os.path.isfile(path):
                    error_msg = f"File not found: {os.path.basename(path)}"
                else:
                    result = self._add_file_to_library(path)
                    if result is True:
                        success_count = 1
                    elif isinstance(result, str):
                        error_msg = result
                    else:
                        error_msg = "Failed to add file"
            elif mode == "folder":
                if not os.path.isdir(path):
                    error_msg = f"Folder not found: {os.path.basename(path)}"
                else:
                    files = scan_folder(path)
                    if not files:
                        error_msg = "No readable files found in folder"
                    else:
                        for file in files:
                            result = self._add_file_to_library(file)
                            if result is True:
                                success_count += 1
            library = build_library()
            self.push_screen(
                LibraryScreen(library, status_msg=error_msg, success_count=success_count),
                callback=self._handle_library_selection
            )
            return
        if isinstance(result, tuple) and result[0] == "delete":
            new_library = result[1]
            self._rewrite_library(new_library)
            self.action_library()
            return

        if result:
            self._load_file(result)
    
    def _load_file_internal(self, start_scroll):
        paras = load_or_parse(self.file_path)
        
        self.reader = Reader(paras=paras, width=max_width)
        self.reader.scroll = start_scroll
        
        state = load_state(self.file_path)
        state["total_lines"] = self.reader.total_lines
        state["timestamp"] = datetime.now().isoformat()
        save_state(self.file_path, state)
        
        self.update_view()
    
    def _load_file(self, file_path):
        self.file_path = file_path
        
        saved_state = load_state(self.file_path)
        saved_scroll = saved_state.get("scroll", 0)
        total_lines = saved_state.get("total_lines", 1)
        
        if saved_scroll > 0:
            self.push_screen(
                ResumePrompt(self.file_path, saved_scroll, total_lines),
                callback=self._handle_resume_choice
            )
        else:
            self._load_file_internal(0)
    
    def _add_file_to_library(self, path):
        try:
            path = path.strip().strip('"').strip("'").strip("'").strip("'")
            path = os.path.abspath(os.path.expanduser(path))
            path = path.replace("'", "'").replace("'", "'")
            if not os.path.isfile(path):
                return f"Not a file: {os.path.basename(path)}"
            if not any(path.lower().endswith(ext) for ext in exts):
                return f"Unsupported file type: {os.path.basename(path)}"
            state = load_state(path)
            if state and state.get("timestamp"):
                return True
            paras = load_or_parse(path)
            temp_reader = Reader(paras=paras, width=max_width)
            state = {
                "scroll": 0,
                "timestamp": datetime.now().isoformat(),
                "bookmarks": [],
                "total_lines": temp_reader.total_lines
            }
            save_state(path, state)
            return True
        except Exception as e:
            return f"Error: {str(e)[:50]}"
    def _rewrite_library(self, library):
        with open(state_file, "r") as f:
            state = json.load(f)

        keep = {item["path"] for item in library}
        for key in list(state.keys()):
            if key not in keep and key != "_global":
                state.pop(key, None)

        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
        
        
    def _handle_theme_selection(self, theme_name: str | None):
        if theme_name:
            self._current_theme = THEMES[theme_name]
            save_theme(theme_name)
            self.apply_theme()

    def action_quit(self):
        self._voice_active = False
        self._auto_scroll_active = False
        if self.reader and self.file_path:
            state = load_state(self.file_path)
            state["scroll"] = self.reader.scroll
            state["total_lines"] = self.reader.total_lines
            state["timestamp"] = datetime.now().isoformat()
            save_state(self.file_path, state)
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


class ThemeSelector(Screen):
    CSS = """
    ThemeSelector {
        background: black;
        align: center middle;
    }
    #box {
        width: 40;
        padding: 1 2;
        border: round white;
    }
    .selected {
        background: #444444;
    }
    """
    def __init__(self):
        super().__init__()
        self.selected_index = 0
    
    def compose(self):
        with Vertical(id="box"):
            yield Static("Select Theme\n")
            for i, theme_name in enumerate(THEMES.keys()):
                yield Static(
                    theme_name.capitalize(),
                    classes="selected" if i == self.selected_index else ""
                )
    def on_key(self, event: Key):
        if event.key == "up":
            old_index = self.selected_index
            self.selected_index = max(0, self.selected_index - 1)
            if old_index != self.selected_index:
                self._update_selection()
        elif event.key == "down":
            old_index = self.selected_index
            self.selected_index = min(len(THEMES) - 1, self.selected_index + 1)
            if old_index != self.selected_index:
                self._update_selection()
        elif event.key == "enter":
            theme_name = list(THEMES.keys())[self.selected_index]
            self.dismiss(theme_name)
        elif event.key.lower() == "q":
            self.dismiss(None)
    
    def _update_selection(self):
        statics = self.query(Static)
        for i, static in enumerate(list(statics)[1:]):
            if i == self.selected_index:
                static.add_class("selected")
            else:
                static.remove_class("selected")

class VoiceSetupScreen(Screen):
    CSS = """
    VoiceSetupScreen {
        background: black;
        align: center middle;
    }
    #box {
        width: 70;
        padding: 1 2;
        border: round white;
    }
    """
    def __init__(self, model_missing=False):
        super().__init__()
        self.model_missing = model_missing
    
    def compose(self):
        if self.model_missing:
            msg = [
                "Voice Control - Model Missing\n",
                "Vosk model not found.\n",
                "Download: https://alphacephei.com/vosk/models",
                "Extract to: ~/.reader_app/vosk-model/\n",
                "Press any key to close"
            ]
        else:
            msg = [
                "Voice Control - Dependencies Missing\n",
                "Install required packages:",
                "  pip install vosk pyaudio\n",
                "Then download Vosk model:",
                "  https://alphacephei.com/vosk/models",
                "Extract to: ~/.reader_app/vosk-model/\n",
                "Press any key to close"
            ]
        
        yield Vertical(
            *[Static(line) for line in msg],
            id="box"
        )
    
    def on_key(self, event: Key):
        self.dismiss()

class VoiceInstallScreen(Screen):
    CSS = """
    VoiceInstallScreen {
        background: black;
        align: center middle;
    }
    #box {
        width: 70;
        padding: 1 2;
        border: round white;
    }
    """
    def compose(self):
        yield Vertical(
            Static("Voice Control Setup\n"),
            Static("Installing dependencies...\n", id="status"),
            Static("This may take a minute."),
            id="box"
        )
    
    def on_mount(self):
        self.run_worker(self._install_dependencies(), exclusive=True)
    
    async def _install_dependencies(self):
        try:
            await asyncio.sleep(0.1)
            status_widget = self.query_one("#status", Static)
            status_widget.update("Installing vosk...")
            result1 = subprocess.run(
                [sys.executable, "-m", "pip", "install", "vosk"],
                capture_output=True,
                text=True
            )
            
            status_widget.update("Installing pyaudio...")
            result2 = subprocess.run(
                [sys.executable, "-m", "pip", "install", "pyaudio"],
                capture_output=True,
                text=True
            )
            if result1.returncode == 0 and result2.returncode == 0:
                status_widget.update(
                    "✓ Dependencies installed!\n\n"
                    "Download Vosk model:\n"
                    "https://alphacephei.com/vosk/models\n"
                    "Extract to: ~/.reader_app/vosk-model/\n\n"
                    "Restart the app to use voice control.\n\n"
                    "Press any key to close and restart"
                )
            else:
                error_msg = result1.stderr if result1.returncode != 0 else result2.stderr
                status_widget.update(
                    f"✗ Installation failed\n\n"
                    f"Error: {error_msg[:100]}\n\n"
                    f"Try manually: pip install vosk pyaudio\n\n"
                    f"Press any key to close"
                )
        except Exception as e:
            try:
                status_widget = self.query_one("#status", Static)
                status_widget.update(
                    f"✗ Installation failed\n\n"
                    f"Error: {str(e)}\n\n"
                    f"Try manually: pip install vosk pyaudio\n\n"
                    f"Press any key to close"
                )
            except:
                pass
        
        await asyncio.sleep(1)
    
    def on_key(self, event: Key):
        self.dismiss(False)

class VoiceSetupScreen(Screen):
    CSS = """
    VoiceSetupScreen {
        background: black;
        align: center middle;
    }
    #box {
        width: 70;
        padding: 1 2;
        border: round white;
    }
    """
    def __init__(self, model_missing=False):
        super().__init__()
        self.model_missing = model_missing
    
    def compose(self):
        if self.model_missing:
            msg = [
                "Voice Control - Model Missing\n",
                "Vosk model not found.\n",
                "Download: https://alphacephei.com/vosk/models",
                "Extract to: ~/.reader_app/vosk-model/\n",
                "Press any key to close"
            ]
        else:
            msg = [
                "Voice Control - Dependencies Missing\n",
                "Install required packages:",
                "  pip install vosk pyaudio\n",
                "Then download Vosk model:",
                "  https://alphacephei.com/vosk/models",
                "Extract to: ~/.reader_app/vosk-model/\n",
                "Press any key to close"
            ]
        
        yield Vertical(
            *[Static(line) for line in msg],
            id="box"
        )
    
    def on_key(self, event: Key):
        self.dismiss()

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
        
        if total_lines is None or total_lines <= 1:
            self.progress = 0
        else:
            self.progress = int((self.scroll / max(total_lines - 1, 1)) * 100)

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

class LibraryScreen(Screen):
    CSS = """
    LibraryScreen {
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
    def __init__(self, library, status_msg=None, success_count=0):
        super().__init__()
        self.library = library
        self.index = 0
        self.input_mode = None
        self.buffer = ""
        self.search_mode = False
        self.search_buffer = ""
        self.filtered_library = library
        self.status_msg = status_msg
        self.success_count = success_count
    def compose(self):
        with Vertical(id="box"):
            title_text = "Library  (A=add file, F=add folder, /=search)\n"
            if self.status_msg:
                title_text = f"❌ {self.status_msg}\n"
            elif self.success_count > 0:
                title_text = f"✓ Added {self.success_count} file(s)\n"
            yield Static(title_text, id="title")
            for i, item in enumerate(self.filtered_library):
                progress = f" - {item['progress']}%" if item['progress'] < 100 else ""
                yield Static(
                    f"{os.path.basename(item['path'])}{progress}",
                    classes="selected" if i == self.index else ""
                )
    def on_paste(self, event: textual.events.Paste):
        if self.input_mode:
            self.buffer += event.text
            self._update_input_prompt()
    
    def on_key(self, event: Key):
        if self.search_mode:
            if event.key == "enter":
                self._exit_search_mode()
                return
            elif event.key in ("escape",):
                self.search_buffer = ""
                self.filtered_library = self.library
                self._exit_search_mode()
                return
            elif event.key == "backspace":
                self.search_buffer = self.search_buffer[:-1]
                self._update_search()
            elif event.character and event.character.isprintable():
                self.search_buffer += event.character
                self._update_search()
            return
        if self.input_mode:
            if event.key == "enter":
                path = self.buffer.strip()
                self.dismiss((self.input_mode, path))
                return

            elif event.key in ("escape",):
                self._exit_input_mode()
                return

            elif event.key == "backspace":
                self.buffer = self.buffer[:-1]
                self._update_input_prompt()

            elif event.character and event.character.isprintable():
                self.buffer += event.character
                self._update_input_prompt()

            return
        if event.key == "slash" or event.key == "/":
            self._enter_search_mode()
            return
            
        if event.key.lower() == "a":
            self._enter_input_mode("file")

        elif event.key.lower() == "f":
            self._enter_input_mode("folder")

        elif event.key == "up":
            self.index = max(0, self.index - 1)
            self._update_selection()

        elif event.key == "down":
            self.index = min(len(self.filtered_library) - 1, self.index + 1)
            self._update_selection()

        elif event.key == "enter":
            if len(self.filtered_library) > 0:
                self.dismiss(self.filtered_library[self.index]["path"])

        elif event.key == "delete":
            if len(self.filtered_library) > 0:
                del_item = self.filtered_library[self.index]
                self.library = [item for item in self.library if item["path"] != del_item["path"]]
                self.dismiss(("delete", self.library))
        elif event.key == "q" or event.key == "escape":
            self.dismiss(None)
    def _update_selection(self):
        statics = self.query(Static)
        for i, static in enumerate(list(statics)[1:]):
            if i == self.index:
                static.add_class("selected")
            else:
                static.remove_class("selected")
    def _enter_input_mode(self, mode):
        self.input_mode = mode
        self.buffer = ""
        title = self.query_one("#title", Static)
        prompt = "Enter file path: " if mode == "file" else "Enter folder path: "
        title.update(f"Library - {prompt}")
    def _exit_input_mode(self):
        self.input_mode = None
        self.buffer = ""
        title = self.query_one("#title", Static)
        title.update("Library  (A=add file, F=add folder)\n")
    def _update_input_prompt(self):
        title = self.query_one("#title", Static)
        prompt = "Enter file path: " if self.input_mode == "file" else "Enter folder path: "
        title.update(f"Library - {prompt}{self.buffer}")
    def _enter_search_mode(self):
        self.search_mode = True
        self.search_buffer = ""
        title = self.query_one("#title", Static)
        title.update("Library - Search: ")
    def _exit_search_mode(self):
        self.search_mode = False
        title = self.query_one("#title", Static)
        title.update("Library  (A=add file, F=add folder, /=search)\n")
    def _update_search(self):
        title = self.query_one("#title", Static)
        title.update(f"Library - Search: {self.search_buffer}")
        if self.search_buffer:
            q = self.search_buffer.lower()
            self.filtered_library = [
                item for item in self.library
                if q in os.path.basename(item["path"]).lower()
            ]
        else:
            self.filtered_library = self.library
        self.index = 0
        self._rebuild_list()
    def _rebuild_list(self):
        container = self.query_one("#box")
        statics = list(container.query(Static))
        for static in statics[1:]:
            static.remove()
        for i, item in enumerate(self.filtered_library):
            progress = f"{item['progress']}%" if item['progress'] < 100 else ""
            new_static = Static(
                f"{os.path.basename(item['path'])} {progress}".strip(),
                classes="selected" if i == self.index else ""
            )
            container.mount(new_static)
if __name__ == "__main__":
    if len(sys.argv) == 2:
        app = ReaderApp(sys.argv[1])
    else:
        app = ReaderApp(None)
    app.run()