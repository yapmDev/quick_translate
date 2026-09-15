# quick-translate

A lightweight translation panel for Linux/GNOME, backed by Google's
unofficial translation endpoint (the same one Crow Translate and Dialect use).
Nothing is stored: no history, no cache, no settings.

## Features

- Tray icon — open the panel
- Quick translate via hotkey (`SIGUSR1`) — raises the panel, pre-filled with
  the clipboard and translated on the spot whenever the copy is recent (under
  30 s); otherwise it opens untouched
- Translates as you type (debounced), detecting the source language
- Every language the endpoint supports, picked in the toolbar: source selector,
  swap button, target selector. The source selector also shows what was
  detected (`Automático · Inglés`)
- Swap button: the languages trade places and the translation becomes the text
  to translate
- Follows the system theme (GTK3)
- systemd user service friendly

## Requirements

- Linux / GNOME, X11 or XWayland
- Python 3.12+, PyGObject (`gi`), GTK 3, `gir1.2-ayatanaappindicator3-0.1`
- An internet connection (no API key needed)

## Run

```bash
python3 main.py
```

## Autostart (systemd user service)

`~/.config/systemd/user/quicktranslate.service`:

```ini
[Unit]
Description=quicktranslate
After=graphical-session.target

[Service]
ExecStart=/usr/bin/python3 %h/Projects/quick_translate/main.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now quicktranslate.service
```

## Hotkey

Bind `quick_translate_trigger.sh` to a GNOME custom shortcut (Settings →
Keyboard → Custom Shortcuts). It finds the running process and sends it
`SIGUSR1`, which brings up the panel on the clipboard. The lookup matches the
process's full path, so the app has to be started by absolute path — which the
service above does.

## License

MIT
