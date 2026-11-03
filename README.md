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
| `[school]` / `[!]` / `[3d printer]` in the subject or text | that hand-drawn symbol prints above the heading |
| An email with a photo | **one** printout (all the text in one box) with the photo underneath |
| Subject line | printed as the bold heading; no subject → no heading |
| An email **forwarded** to the printer, with your own notes typed above it | `EMAIL` printout: bold subject, a 3-5 sentence summary (**requests and deadlines first**, signature and logos stripped), and an **ACTIONS** checklist from your notes |
| `[reply by friday]` / `[paper due monday]` / `[full]` in your notes above a forward | those answers pulled out of the email under their own headings / the whole message instead of a summary |
| A Google Calendar event | `EVENT` printout at 7 am the day before (or 7 am the day of, if it was added after the cutoff) |

## Full setup from a blank SD card (recommended: Pi 3B+ with local AI)

**You need:** the Pi 3B+, a microSD card (16 GB+), a good 5 V / 2.5 A power supply, the TM-T82II on USB, and a computer with
[Raspberry Pi Imager](https://www.raspberrypi.com/software/).

**1. Flash the card.** In Raspberry Pi Imager: *Raspberry Pi Device* = **Raspberry Pi 3**; *Operating System* = *Raspberry Pi OS (other)* ->
**Raspberry Pi OS Lite (64-bit)**; choose the card; Next -> **Edit settings**:
* *General:* hostname `jack-in-a-box`, a username + password, your Wi-Fi name/password + country (skip Wi-Fi if you'll use an Ethernet cable), your timezone.
* *Services:* **Enable SSH** (password authentication).

*Lite* has no desktop, which is exactly the "super basic" mode the AI wants, and **64-bit** is required for the AI (the 32-bit image can't run Ollama).
Write the card, put it in the Pi, plug in the printer (USB) and the power.

**2. Make a read-only GitHub token** (once; the repo is private): GitHub -> Settings -> Developer settings -> Personal access tokens ->
*Fine-grained tokens* -> repository `jack_in_a_box`, permission **Contents: Read-only**, expiry 1 year. Copy it.

**3. SSH in** (give it a minute or two to boot): `ssh YOURUSER@jack-in-a-box.local` (or use the Pi's IP from your router's device list). Then paste:

```bash
sudo apt-get update && sudo apt-get install -y git
read -rsp "Paste GitHub token: " T; echo
git clone "https://jackallen28:${T}@github.com/jackallen28/jack_in_a_box.git" && unset T
bash jack_in_a_box/deploy/install.sh --with-ai --reboot
```
That installs everything, sets it to start on every boot, turns on hourly auto-updates, frees RAM for the AI and installs Ollama plus the small model
(about 10-20 minutes, mostly downloading), then reboots by itself. Drop `--with-ai` (and `--reboot`) if you don't want the AI; you can add it later with
`bash ~/jack_in_a_box/deploy/enable-ai.sh`.

**4. In your browser** (a minute after the reboot): `http://jack-in-a-box.local:8080` -> choose a password -> **Printer** tab (*Check connection*, *Print a test
reminder*) -> **Email** (Gmail address + app password, your addresses) -> **Calendar** (iCal link) -> **Messages** (friends, button) ->
**Text & sorting** -> *Test AI summary*, and when it works press **Switch on AI mode**.

The AI is installed but **OFF** until you press that button, so the box works with the built-in summary from the first minute.

## Install (Raspberry Pi 3B+ or newer, Raspberry Pi OS)

One command, run on the Pi (over SSH is fine). It installs everything, sets the app to **start
automatically every time the Pi boots** (and restart itself if it ever crashes), and starts it now:

```bash
curl -fsSL https://raw.githubusercontent.com/jackallen28/jack_in_a_box/main/deploy/bootstrap.sh | bash
```

**The repo is private**, so the one-liner above won't work (GitHub won't serve the file without a login). Make a read-only
token once (GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens**: repository
`jack_in_a_box`, permission **Contents: Read-only**, expiry 1 year), then on the Pi paste it **once**:

```bash
read -rsp "Paste GitHub token: " T; echo
git clone "https://jackallen28:${T}@github.com/jackallen28/jack_in_a_box.git" && unset T
bash jack_in_a_box/deploy/install.sh
```
(`read -s` keeps the token out of your shell history.) The installer moves the token into a private file on the Pi
(`data/.git-credentials`, readable only by you) and removes it from the clone's URL, so it never needs typing again.

## Automatic updates

Push to the `jack-in-a-box` branch on GitHub and the Pi picks it up by itself, within about an hour:

1. A systemd timer checks GitHub hourly (and 3 minutes after boot).
2. If there are new commits it switches to them, reinstalls Python packages **only if** `requirements.txt` changed, and restarts the app.
3. It then waits for the web page to answer. **If it doesn't, it rolls back to the previous version** and won't retry that
   commit until you push a newer one.
4. Your settings, history and token (`data/`) are never touched.

See the result on the **Account** tab (current version, last check, *Check & update now*), or switch auto-update off there.
It runs as your normal user; its only extra permission is a sudo rule for exactly two commands (restart this service, start the
updater). Updates need the git-clone install above, not the ZIP route.
`journalctl -u jack-in-a-box-update -n 30` shows what it did.

**Token expiry:** fine-grained tokens expire. If updates start failing with "Couldn't reach GitHub", make a new token and re-run
`bash ~/jack_in_a_box/deploy/install.sh`; it will ask for it.

Updating by hand any time: `bash ~/jack_in_a_box/deploy/update.sh --force`, or re-run the installer
(your settings and history in `data/` are kept).

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

## Symbols: [school], [!], [3d printer] ...

Put a symbol's name in **square brackets** anywhere in an email's **subject or text** and a hand-drawn symbol prints in a row
**above the heading** (under the `REMINDER` title; the order is changeable per template):

```
Subject:  [school] Parent forms
Body:     [!] sign the excursion form by friday
          pack lunch tomorrow [fun]
```
* **Full or short names**, any capitalisation, spaces/dashes ignored: `[School]`, `[uni]`, `[!]`, `[?]`, `[3D Printer]` = `[3dprinter]` = `[3d]`.
  Several are fine: `[work][!]`. Up to 6 per printout (shrinks to fit).
* **Where a tag applies:** in the **subject**, or alone on the **first line**, it goes on *every* reminder from that email. On a task's
  own line it goes on just that task. A line holding *only* tags decorates the next task.
* **Unknown tags** (like `[draft]` or `[1]`) are left as ordinary text and never spell-corrected.
* Works on emails, forwarded-email summaries (tags in the subject or your own notes), friends' messages, the Print page and
  calendar event titles/descriptions (`[school] Parent night`).

24 symbols come built in, drawn in a sketchy hand-drawn style: `!` `?` work school fun holiday calendar 3d printer accountant
bookwork home shopping money phone email birthday heart star idea health car pet clock done. **Print the cheat-sheet** from the
**Symbols** tab to stick on the fridge.

**Add your own** on the Symbols tab: upload a picture (black-on-white works best) **or draw one with the mouse / your finger**, give it a
name and optional extra tag names, then Save. You can also add extra tag names to any built-in symbol, or switch ones you don't want off.

## Forwarded emails: summary, [focus] and [full]

Forward an email to the box and type your own notes **above** it. The printout is `EMAIL`, your subject in bold, then:

```
REPLY BY FRIDAY            <- each [focus] you typed, with the sentence(s) from the email that answer it
Please let me know by Friday so we can close the quarter.
PAPER DUE MONDAY
The Year 9 science paper is due Monday 9 November.
SUMMARY                    <- the rest, requests/deadlines first (never repeats what's above)
...
ACTIONS                    <- your own notes, as a tick-box list
[ ] Ring Dave about the credit
```
* **`[reply by friday]`, `[paper due monday]`, `[cost]`, `[bank details]` ...** anything in square brackets that isn't a symbol is a
  *question*. The box finds the sentences that best match it (words and sense: *reply* also finds "let me know", "get back to", "respond";
  *cost* finds dollar amounts). It only ever quotes the email, and if it isn't mentioned it prints **"Not mentioned in this email."** so
  you know it wasn't missed. `[actions]` and `[deadlines]` pull out the two strongest requests / dates.
* **`[full]`** prints the whole message instead of a summary (capped at 2,500 characters, adjustable).
* **Symbols still work** in the same notes: `[work] [!] [reply by friday]`.
* **Signatures never print.** Before anything is summarised or printed, the box removes sign-offs and contact blocks ("Kind regards, Dave /
  Accounts Manager / T: 02 ..."), "Sent from my iPhone", legal disclaimers, "CAUTION: external email" banners, older replies in the
  chain ("On Monday ... wrote:", Outlook From/Sent blocks), long links (shown as "(link)") and the `[cid:image001.png]` / `[image: Logo]`
  placeholders that signature pictures leave behind. Pictures in forwarded emails are never printed. Real text is kept: it only cuts what
  looks like a signature.

## Local AI summaries (Raspberry Pi 3B+)

Optional, and the built-in summary is already decent. The AI rewrites the *rest-of-the-email* summary only; `[focus]` and `[full]` never use it.
Expect **roughly 1-3 minutes per forwarded email** on a 3B+. I could not run a real model while building this, so treat that figure
as an estimate; **Text & sorting -> Test AI summary** tells you the truth on your Pi.

**One-off setup** (already done by `install.sh --with-ai`; to add it to an existing install, run `bash deploy/enable-ai.sh`):
1. **64-bit OS.** Ollama only runs on 64-bit; `uname -m` should say `aarch64`. If it says `armv7l`, the script stops without changing anything:
   reflash with *Raspberry Pi OS Lite (64-bit)* (see *Full setup* above), reinstall, copy your `data/` folder back.
2. `lean-mode.sh` (run by the above): GPU memory to 16 MB, Bluetooth and unused services off, compressed swap (zram). Reversible with `--undo`.
3. `ai-setup.sh` (run by the above): installs Ollama, sets it to one model at a time and to free its RAM straight after each use, downloads
   `qwen2.5:0.5b` (about 400 MB, the biggest that fits 1 GB).
4. Reboot, then **Text & sorting -> Test AI summary**. When it works, press **Switch on AI mode** (it refuses if the AI isn't ready, and
   *Switch back to built-in summary* undoes it).

**Why it won't make things up:** a 0.5B model will happily invent facts, so it's never trusted. It only sees a short excerpt (the requests and
deadlines the ranker picked, plus sender and subject), and every number, date, day/month and name in its answer must appear in the email. Otherwise
its answer is **thrown away and the built-in summary is used**. The same happens if Ollama is off, the model is missing or it times out (4 minutes).
Only one summary runs at a time. The history/Try-it notes say whether the AI or the built-in summary was used. The "Try it" preview never uses the AI.

Prefer not to run it on the Pi? Set *AI: Ollama address* to another computer on your network (and `OLLAMA_HOST=0.0.0.0` there).

## Power, cooling and the email mailbox

**What it costs to run:** a Pi 3B+ idles around 2-3 W, roughly 20-25 kWh a year, which is about **$6-9 a year** at ~30c/kWh. The printer only
draws noticeably while printing. Leave it on 24/7; there is nothing to tune for cost.
* **Fan** (optional): a GPIO pin switches a 5 V fan through a transistor. *Automatic* runs it above 60 C (off below 50 C), and for the whole of every AI
  summary, then lets it cool. Wiring guide and settings on the **Power & fan** page; GPIO22 by default.
* **`bash deploy/eco-mode.sh`** (optional, reversible): LEDs, Bluetooth and HDMI off, Wi-Fi power-save. Saves a few hundred milliwatts.
* **Power off when idle** (optional, **off by default**): a 3B+ has no sleep mode and no clock to wake itself, so this fully switches it off after
  N idle hours (never while anything is queued or a calendar print is near); wake it by a button on GPIO3 + GND, or re-plug the power. Most
  people should leave it off.
* **Email left in the inbox:** after an email is printed (or queued for the button) it is **deleted**: on Gmail it moves to Trash, which empties itself
  after 30 days, so a mistake is recoverable. Only emails that were actually printed or queued are deleted; blocked/unknown senders, empty emails, a
  friend's message that was dropped because the queue was full, and anything that errored are kept (marked read). Change this under *Email -> After
  printing an email* (delete / mark read / move to a folder). If the Pi was off, every unread email is picked up in order when it starts.

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
* **Summaries.** Frequency-based extractive summarising: it picks the 3-5 most informative sentences and prints them with
  **requests and deadlines first** (sentences that ask for something, or contain a date, "by/before/due", or an amount, are
  always kept and lead). It quotes rather than paraphrases. See *Forwarded emails* and *Local AI* below.

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
bash ~/jack_in_a_box/deploy/printer-ethernet.sh
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
* Summaries, `[focus]` and `[full]` only read the email text, not attachments (PDF/Word) or pictures. The built-in summary quotes, it doesn't reword.
* `[focus]` matching is word-and-synonym based, so an unusual phrasing can be missed (it then says "Not mentioned"). Tell me which ones and I'll add synonyms.
* Local AI, the fan, GPIO and the installers were tested against simulated hardware (a fake Ollama server, mock GPIO pins, a local git remote), not a real Pi.
* HEIC photos need `pip install pillow-heif`.
