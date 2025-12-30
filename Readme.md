# TUI Reader

A Terminal User Interface (TUI) reader for text files, Markdown documents, and PDFs with progress tracking, bookmarking,library and voice control.

## Features

### Core Reading
- **All Format Support**: Read `.txt`, `.md`, `.pdf` files and many more but mainly these 3 only hehe
- **Progress Tracking**: Automatic save/restore of reading position
- **Resume Prompt**: Pick up where you left off with progress percentage display
- **Cache Files**: Saves parsed text from pdfs into json into paras so we don't need to parse the whole pdf everytime we open
- **Lazy PDF Loading**: Efficient memory usage for large PDF files

### Navigation
- **Accelerated Scrolling**: Hold `j` or `k` for progressive speed acceleration
- **Bookmark System**: Create and manage multiple bookmarks per file ( m to open bookmarks and b to create a bookmark at current line )
- **Table of Contents**: Navigate Markdown files by headers
- **PDF Page Navigation**: Jump directly to specific page numbers in PDFs
- **Library Management**: Centralized library view with recent files and progress

### Customization
- **Multiple Themes**: 
    I added only dark paper and sepia but you can add as many as you want :D
- **Theme Selector**: Browse and preview themes with keyboard navigation

### Voice Control (Optional)
- **Offline Recognition**: Uses Vosk for local speech recognition
- **Hands-Free Reading**: Auto-scroll with voice commands
- **Speed Control**: Adjust scrolling speed via voice
- **Cross-Platform**: Works on Windows, macOS, and Linux

### Data Management
- **Smart Caching**: Parsed content cached for fast reloading
- **Progress Persistence**: Reading position saved every 10 scrolls
- **Library Sync**: Automatic tracking of all opened files
- **Search Functionality**: Filter library by filename

## Installation

### Basic Installation

```bash
pip install textual pdfminer.six
```

### Voice Control (Optional)

```bash
pip install vosk pyaudio
```

Download a Vosk model from https://alphacephei.com/vosk/models (recommended: `vosk-model-small-en-us-0.15`)

Extract to: `~/.reader_app/vosk-model/`

**Note**: Pressing `v` will automatically install dependencies if not present.

## Usage

### Launch

```bash
python main.py document.txt

python main.py
```

### Keyboard Commands

#### Reading View

`j` Scroll down one line (hold for acceleration)
`k` Scroll up one line (hold for acceleration)
`t` Show TOC (Markdown only)
`b` Add/update bookmark at current position
`m` Show all bookmarks for current file
`p` Navigate PDF pages (PDF only)
`v` Toggle voice control on/off
`T` Toggle themes
`Ctrl+T` Open theme selection screen
`q` Exit and save progress
`Ctrl+C` Traditional exit

#### Library Screen

`↑/↓` Move selection up/down
`Enter` Open selected file
`a` Add single file to library
`f` Scan and add all compatible files from folder
`/` Filter library by filename
`Delete` Delete selected file from library
`q`/`Esc` Return to reading or exit

#### Bookmark/TOC/Pages Screen

`↑/↓` Move selection up/down
`Enter` Jump to selected location 
`Delete` Delete selected bookmark
`0-9` Jump between pages
`q` Close

#### Theme Selector

`↑/↓` Move selection up/down
`Enter` Apply selected theme
`q` Close

### Voice Commands
When voice control is active (`v`), the following commands are recognized:
`scroll` Start scrolling
`stop` Stop scrolling
`faster` Increase auto scroll speed
`slower` / `slow` Decrease auto scroll speed
`up` Scroll up one line
`down` Scroll down one line

## Configuration

### Storage Location

All data stored in: `~/.reader_app/`

```
~/.reader_app/
├── state.json          # Reading progress and bookmarks
├── cache/
│   └── *.json         # Parsed file cache
└── vosk-model/        # Voice recognition model
```