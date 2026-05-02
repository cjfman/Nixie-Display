# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A controller for a 16-tube 14-segment nixie display. It has two major components:

1. **Python host software** (`pyxielib/`, `run_display`, `send_text`) — runs on a Raspberry Pi or dev machine, schedules and renders display programs, communicates with hardware
2. **Arduino firmware** (`nixie-control-board/`) — C/C++ sketch that receives serial commands from the host and drives the tubes via SPI

## Common commands

### Python (host software)
```bash
pip install -r requirements.txt

# Run with terminal output (for development — renders segments as ASCII art)
./run_display -c terminal

# Send a single message and exit
./send_text -c terminal "Hello World"

# Run against a connected Arduino over serial
./run_display -c serial -s /dev/ttyACM0
./send_text -c serial -s /dev/ttyACM0 "Hello"

# Run on the Raspberry Pi directly (requires spidev and RPi.GPIO)
./run_display -c raspi --keyboard-event-file /dev/input/event0
```

### Arduino firmware
```bash
make all       # compile
make upload    # compile and flash
make connect   # open minicom serial monitor at 115200 baud
```

### Test scripts
Individual feature tests live in `scripts/`:
```bash
python scripts/test_clock.py
python scripts/test_marquee.py
python scripts/test_stockticker.py
python scripts/test_schedule.py
# etc.
```

## Architecture

### Data flow

```
CronScheduler → Program.makeAnimation() → Animation
                                              ↓
                                        Assembler (thread)
                                              ↓
                                        Controller.send(code)
                                              ↓
                              TerminalController / SerialController / RaspberryPiController
```

### The "code" string format

The wire format between Python and the display is a plain string where:
- Printable ASCII characters map to 14-segment bitmaps via `decoder.py`
- `:` after a character sets the colon bit on the previous tube
- `!` after a character sets the underline bit on the previous tube
- `{0x1A2B}` inserts a raw 16-bit hex bitmap
- `{!ABC}` renders A, B, C all with underline

`tube_manager.cmdDecodePrint(code)` parses this format into a list of 16-bit bitmaps. This same logic exists in both Python (`pyxielib/tube_manager.py`) and C (`nixie-control-board/tube_manager.c`).

### Key classes

**Controllers** (`pyxielib/controller.py`): Abstract `Controller` with `send(code)` and `enable()`/`disable()`. Three implementations:
- `TerminalController` — renders to stdout as ASCII segment art, used for development
- `SerialController` — sends `print:<code>\n\r` over serial to the Arduino
- `RaspberryPiController` — decodes bitmaps and sends them directly over SPI (requires `spidev` and `RPi.GPIO`)

**Assembler** (`pyxielib/assembler.py`): Runs a background thread that calls `animation.updateFrameSet()` every 10ms and calls `controller.send()` when a new frame is ready. It holds the currently active `Animation`.

**Scheduler** (`pyxielib/scheduler.py`): `CronScheduler` maintains a list of `(cron_expr, priority, program)` tuples. Each tick it finds the next due program and calls `assembler.setAnimation()`. Falls back to a `default` program (clock) when idle.

**Programs** (`pyxielib/program.py`, `pyxielib/stockticker.py`): Each program implements `makeAnimation() -> Animation`. Programs are stateful — `update()` calls `makeAnimation()` and returns `True` if the animation changed. Key programs: `ClockProgram`, `RssProgram`, `WeatherProgram`, `SleepProgram`, `WakeProgram`, `StockTicker` (runs its own background thread to fetch S&P 500 quotes from yfinance).

**Animations** (`pyxielib/animation.py`, `pyxielib/animation_library.py`): 
- `MarqueeAnimation` — scrolls text across the 16-tube display
- `TubeAnimation` — per-tube timed frame sequences (`TubeSequence`)
- `FullFrameAnimation` — timed sequence of full 16-tube snapshots
- `FileAnimation` — loads from a `.ani` file (custom DSL)
- Looped variants: `LoopedTubeAnimation`, `LoopedFullFrameAnimation`

**UserMenuProgram** (`pyxielib/usermenuprogram.py`): Keyboard-driven interactive menu activated by Ctrl+Alt+F4 (via `evdev`). Uses `Navigator` + `Menu`/`MenuItem` hierarchy from `navigator.py`. Menu items in `menu_library.py` include WiFi management, animations browser, IP display, sleep/wake, reboot, shutdown.

### Animation file format (`.ani`)

Files in `animations/` use a custom DSL parsed by `FileAnimation`:
- `sprite|name|0xHEX` — define a named 16-bit bitmap
- `segment|name|{sprite1}{sprite2}...` — define a named sequence of characters and sprites
- `frame|delay_secs|<content>` — add a full frame composed of characters, sprites, and segments; delay=0 overlays on previous frame
- `scale|factor` — multiply all delays
- `sequence|start|name` / `sequence|end` / `sequence|insert|name` — named reusable frame sequences
- `{N}` in content is a multiplier; `{sprite_name}` expands a named sprite/segment

### Production deployment

The production system runs on a Raspberry Pi. `raspi_run` is the startup script: it pulls `nixie-live` branch from git, then launches `run_display -c raspi`. Logs go to `~/logs/nixie.log` and `~/logs/nixie.stderr`.

The live branch is `nixie-live`. `master` is the development branch.
