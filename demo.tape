# Regenerates the README demo (WebP).  Run:  vhs demo.tape
#
# Requires:
#   - vhs installed (https://github.com/charmbracelet/vhs)
#   - an iPhone connected over USB, unlocked, and trusted
#   - `dcimport` on PATH (e.g. `uv tool install .` from this repo)
#
# Tune Sleep to roughly your transfer time, and Height to just fit the output
# (crop empty space). Resolution is deliberately high: a big FontSize + Width
# render sharp text that GitHub downscales — a small canvas comes out blurry.

Output .assets/demo.webp

Set Shell "bash"
Set FontSize 22
Set Width 1200
Set Height 240
Set Padding 20
Set TypingSpeed 50ms
Set Theme "Catppuccin Mocha"

# --- off-camera setup: clean prompt + fresh output dir (so it's a real import) ---
Hide
Type `export PS1="\$ "`
Enter
Type "rm -rf /tmp/dcimport-demo"
Enter
Ctrl+L
Show

# --- the demo ---
Type "dcimport /tmp/dcimport-demo"
Sleep 200ms
Enter
Sleep 12s      # <- adjust to your import duration

Sleep 2500ms

# --- optional extra beats: uncomment to also show a custom layout ---
# Type "dcimport /tmp/dcimport-demo-by-date --layout '{mtime:%Y}/{mtime:%m}/{name}'"
# Sleep 700ms
# Enter
# Sleep 12s
# Sleep 2s
