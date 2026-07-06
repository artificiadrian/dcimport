# Regenerates the README demo GIF.  Run:  vhs demo.tape
#
# Requires:
#   - vhs installed (https://github.com/charmbracelet/vhs)
#   - an iPhone connected over USB, unlocked, and trusted
#   - `dcimport` on PATH (e.g. `uv tool install .` from this repo)
#
# Tune the Sleep after the import to roughly how long your transfer takes,
# and adjust Width/FontSize if long lines wrap.

Output .assets/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 880
Set Height 140
Set Padding 18
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
