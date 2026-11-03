# Jack in a Box 📦

*Wind it up (send an email) and a reminder pops out.*


Email a note to the box's Gmail address, and your Raspberry Pi prints it as a tidy reminder on an
Epson TM-T82II receipt printer. Everything runs locally on the Pi; you configure it from a web
page at the Pi's IP address.

```
┌──────────────────────────┐
│  R E M I N D E R  (retro)│   <- outlined title
│  Home stuff               │   <- email subject, bold (omitted if there is none)
│ ┌──────────────────────┐ │
│ │ Walk the dog          │ │   <- cleaned-up text in an outlined box
│ │ · · · · · · · · · · · │ │
│ │ DUE                   │ │
│ │ Tomorrow, 5:00 PM     │ │   <- deadline pulled out of the sentence
│ └──────────────────────┘ │
│  [ attached photo ]       │   <- pictures go underneath
└──────────────────────────┘      1 pt black border, at least 80 mm tall, grows with content
```

## What it does

| You send | You get |
|---|---|
| `wahs dishes, walk teh dog at 5pm tomorrow and relpy to work emial by friday` | **3 separate printouts**, spelling fixed, each with its own DUE line, soonest first |
| A paragraph break (blank line) between items | a new printout for each paragraph |
| `Shopping:` followed by `- milk` `- eggs` | one printout with a checklist |
| An email from a **friend** on your list | a `MESSAGE` printout with their name, held until you press the **button** (LED flashes while any wait) |
| Text typed on the web page's **Print** tab | prints straight away, same cleanup as email, photos optional |
| An email with a photo | **one** printout (all the text in one box) with the photo underneath |
| Subject line | printed as the bold heading; no subject → no heading |
| An email **forwarded** to the printer, with your own notes typed above it | `EMAIL` printout: bold subject, a 3-5 sentence summary, and an **ACTIONS** checklist made from your notes |
| A Google Calendar event | `EVENT` printout at 7 am the day before (or 7 am the day of, if it was added after the cutoff) |

## Install (Raspberry Pi 3B+ or newer, Raspberry Pi OS)

One command, run on the Pi (over SSH is fine). It installs everything, sets the app to **start
automatically every time the Pi boots** (and restart itself if it ever crashes), and starts it now:

```bash
curl -fsSL https://raw.githubusercontent.com/jackallen28/jackallen/claude/youthful-bardeen-b6h2r9/jack-in-a-box/deploy/bootstrap.sh | bash
```

If the GitHub repo is private, the `curl` won't work; use git instead (it will ask for your login/token):

```bash
git clone --depth 1 -b claude/youthful-bardeen-b6h2r9 https://github.com/jackallen28/jackallen.git
bash jackallen/jack-in-a-box/deploy/install.sh
```

Re-run the same command any time to **update** (your settings and history in `data/` are kept).

Then:
1. Open `http://<pi-ip>:8080` (the installer prints it; `http://raspberrypi.local:8080` usually works too).
   **The first visitor chooses the password**, so do this straight away.
2. **Email** tab → Gmail address + app password → **Your addresses (admin)** → Save → *Test connection*.
3. **Printer** tab → *Check connection* → *Print a test reminder*.

Optional: give the Pi a friendly name so the page lives at `http://jack-in-a-box.local:8080`:
`sudo hostnamectl set-hostname jack-in-a-box && sudo reboot`.

Useful commands: `systemctl status jack-in-a-box` · `journalctl -u jack-in-a-box -f` · `sudo systemctl restart jack-in-a-box`

### The Gmail account
1. Create a dedicated Gmail account for the printer.
2. Turn on **2-Step Verification**, then create an **App Password** at <https://myaccount.google.com/apppasswords>.
   Gmail will not accept your normal password over IMAP.
3. Use the 16-letter app password in the **Email** tab. IMAP is on by default.

> **Security.** Anyone who can email that address could otherwise make your printer print.
> So mail is only accepted from your **admin addresses** and your **friends list**, and mail failing Gmail's
> SPF/DKIM checks is dropped (so a forged "From" doesn't work). The email password is stored in
> `data/config.json` (file mode 600) on the Pi. The settings page is password protected, but it
> is plain HTTP on your LAN: don't expose port 8080 to the internet.

## Customising (the web page)

* **Templates** - separate looks for Reminder / Calendar / Email, with a *live preview* that is
  the exact bitmap sent to the printer. Change title text and font (Bungee, Monoton, Press Start 2P,
  Shrikhand, ...), outline / solid / shadow / reversed-bar styles, border thickness and corners,
  section order, text size, upload a logo/graphic, footers, labels (DUE / WHEN / ACTIONS), and
  more. *Duplicate* a template to make extra looks (e.g. one for a client).
* **Calendar** - paste your calendar's iCal URL (Google Calendar → Settings → *Integrate calendar* →
  *Public address in iCal format*, or the *Secret address* for private calendars). Each calendar can
  use its own template (title, picture, layout). The *Upcoming* table shows exactly when each event will print.
* **Text & sorting** - date order (4/11), 12/24 h clock, print order, words never to "correct", urgent keywords.
* **Rules** - "if sender contains `@bigclient.com` → use the Client template, title CLIENT, 2 copies"; tag a boss's
  emails `[BOSS]` and mark them urgent; silence noise. Work for calendar events too.
* **Integrations** - an HTTP endpoint so Zapier / Make / Home Assistant / iOS Shortcuts / your own scripts
  can print (`POST /api/print`), a webhook after each print, and a `plugins/` folder for your own Python.
* **Try it** - type an email and see precisely how it'll be split, corrected and laid out, without printing.
* **Dashboard** - history with thumbnails, *Reprint*, a live activity log, printer status. If the printer is off or out
  of paper, jobs wait and retry in order.

## Messages from friends + the button

On the **Messages** tab you keep a list of friends (name + email). Anyone else who emails the printer is ignored.

* **Your addresses** (the *admin* list on the **Email** tab) always print immediately as reminders.
* **Friends** print as a **MESSAGE**: bold subject as the heading, their text in the box, photos underneath, and
  `from <the name you gave them>` under the title (all editable in the *Message from a friend* template).
  Their text is printed as written (no spell-fixing) unless you turn that on.
* Friend messages **wait in a queue**. If a physical button is wired up, its LED **flashes while anything is waiting**; press it and
  everything waiting prints, oldest first. The dashboard shows the queue too, with *Print now* / *Discard* for each message and for all of them.
* **Skip the queue**: tick it next to a friend and their messages print immediately, like yours. (Or untick
  *Hold friends' messages* to turn queueing off for everyone.)
* A cap on waiting messages (default 50) stops a flood filling the queue.

### Wiring the button and LED (Pi GPIO)
Pin numbers are GPIO/BCM numbers and can be changed on the Messages tab. Defaults:

| Part | Wire to |
|---|---|
| Button, one side | GPIO17 (physical pin 11) |
| Button, other side | any GND (e.g. pin 9). No resistor: the Pi's internal pull-up is used |
| LED + | GPIO27 (pin 13) through a ~330 ohm resistor |
| LED - | GND (e.g. pin 14) |

> **Check your button's LED voltage.** The 330 ohm wiring is for a 3.3 V LED. Many illuminated arcade buttons have a **5 V or 12 V**
> LED; those must **not** be driven straight from a GPIO pin (a pin supplies only a few mA and 3.3 V). Switch the LED from its own supply
> with a small transistor or MOSFET (GPIO27 -> ~1 k ohm -> transistor base/gate). If you wire the LED to 3.3 V instead of to GND, untick *LED lights when the pin is HIGH*.

Then tick **Use a physical button + LED** and Save. *Flash the LED* tests the LED; *Press the button (from here)* does what the button does.
The installer adds you to the `gpio` group; if the page says "GPIO not available", reboot once.

## How the "smart" parts work (no AI needed)

* **Splitting.** Blank lines separate reminders. Inside a line, `and` / commas / `then` split it
  *only where each part starts with an action verb*, so `buy milk, eggs and bread` stays one reminder while
  `wash dishes, walk the dog and reply to work email` becomes three. A one-paragraph email with one task per line is a list of tasks.
* **Dates.** `tomorrow`, `Friday`, `next Friday`, `5pm`, `at 5`, `14/11`, `14th Nov`, `in 2 hours`, `EOD`, `end of month`...
  are lifted out of the sentence into a DUE line. A bare weekday means the *next* one; `this Friday` includes today.
  `at 5` is read as a working-day time (1-6 = pm, 7-11 = am). Dates are resolved relative to when the email was *sent*.
* **Spelling/grammar.** Typo list + a dictionary check (`pyspellchecker`) and rules for `i`→`I`, `dont`→`don't`, `a apple`→`an apple`,
  doubled words, capitals, spacing and full stops. It is deliberately conservative, and can't know your friends' names - add
  anything it wrongly "fixes" under *Words never to correct*. The Try-it page and history list what it changed.
* **Sorting.** Urgent first, then soonest deadline, then undated in the order you wrote them (changeable).
* **Summaries.** Frequency-based extractive summarising: it picks the 3-5 most informative sentences (boosting dates, amounts,
  requests and deadlines) and prints them in their original order. It quotes rather than paraphrases.
  *Optional:* turn on a local model through [Ollama](https://ollama.com) (e.g. `qwen2.5:0.5b`) for abstractive
  summaries. On a Pi 3B+ (1 GB RAM) that is slow (tens of seconds) and modest in quality; it falls back to the built-in
  summariser on any failure.

### Calendar timing
For an event on day **D** with print time **T** (default 07:00):
* app learns of it before **D-1 T** → prints at **D-1 T**;
* learns of it after that but before **D T** → prints at **D T**;
* learns of it later and it hasn't started → prints immediately (or never, your choice);
* optionally also prints a second copy at **D T**.

"Learns of it" is when this app first sees it in the feed (Google doesn't expose creation time reliably). Cancelled events that haven't printed are dropped.
Recurring events work. The Pi must be on at the print time; if it was off, a missed print goes out when it comes back (unless the event has finished).

## Connecting the printer: USB or the Pi's Ethernet port

**USB** (every TM-T82II): plug in, power on. The app finds it automatically (`/dev/usb/lp0`). Nothing to configure.

**Ethernet** *(skip this if your printer is USB-only; most are)*: only for a TM-T82II that has a network card (e.g. the Epson UB-E04 interface). Check by holding **FEED**
while powering the printer on to print its self-test sheet, which lists the interface and IP address.
Cable the printer straight into the Pi's Ethernet port, then:

```bash
bash ~/jack-in-a-box-src/jack-in-a-box/deploy/printer-ethernet.sh     # path from the one-line installer; or deploy/printer-ethernet.sh in your clone
```
then in the web page: **Printer** → Connection *Network* → the printer's IP (Epson cards with no DHCP answer on
`192.168.192.168` by default) → Save → *Check connection*.

**Will this stop me reaching the Pi over Wi-Fi? No.** The Pi's Ethernet port and Wi-Fi are separate interfaces. The script gives
Ethernet a fixed address on its own tiny network (`192.168.192.1`) and tells it never to be the default route, so internet, Gmail,
calendar sync, SSH and the config page all keep using Wi-Fi exactly as before. The only ways to break this:
* **Same subnet.** If your Wi-Fi router already hands out `192.168.192.x`, the script refuses and tells you to pick another range
  (`bash printer-ethernet.sh 10.50.0.1`).
* **Using the Ethernet port for the router too.** The port can serve one cable: either the router or the printer, not both.
  (You'd put the printer on the router's network instead, or on a switch.) Use Wi-Fi for the Pi and Ethernet for the printer.

If you set the connection to *Automatic* the app uses USB when a USB printer is present, otherwise the network IP.

## Printer notes (TM-T82II)
* Paper is 80 mm wide but the printable area is **72 mm (576 dots at 203 dpi)**. Printouts are therefore 72 mm wide and
  at least 80 mm tall (the "80 x 80" minimum), growing with content. Size is adjustable per template.
* The whole printout is sent as one raster image, so fonts, outlines and pictures print exactly as previewed.
  Non-Latin scripts and emoji aren't supported by the bundled fonts (emoji are dropped).
* *Printouts per hour* is a safety limit so a mail loop can't empty a roll.

## Running by hand / developing
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
JACK_IN_A_BOX_DATA=./data .venv/bin/python -m app    # printer "Test mode" saves PNGs to data/dummy
.venv/bin/python -m pytest tests
```
Logs: `journalctl -u jack-in-a-box -f`. Data (config, history, database) lives in `data/` - back it up, and delete it to reset.

Fonts are open-licence (SIL OFL): Bungee, Bungee Shade, Monoton, Press Start 2P, Shrikhand, Courier Prime.

## Known limits
* Gmail only exposes mail via IMAP polling here (default every 30 s), not push.
* Spelling is best-effort; unusual names and slang may be altered or missed. The list of "never correct" words fixes this over time.
* The summariser can't paraphrase or read attachments (PDF/Word) - only the email text.
* HEIC photos need `pip install pillow-heif`.
