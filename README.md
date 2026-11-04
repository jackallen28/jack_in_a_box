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
| A row submitted through your **Google Form** (heading, body, symbols 1-3, image upload) | printed like an email; the row is **deleted from the sheet once it has printed** |
| `[school]` / `[!]` / `[3d printer]` in the subject or text | that hand-drawn symbol prints above the heading |
| An email with a photo | **one** printout (all the text in one box) with the photo underneath |
| Subject line | printed as the bold heading; no subject → no heading |
| An email **forwarded** to the printer, with your own notes typed above it | `EMAIL` printout: bold subject, a few **bullet points** summing up the whole email and its history (what it's about, the key facts, then tick-boxes for what you must do; signature and logos stripped), and an **ACTIONS** checklist from your notes. Add `include image` anywhere in your notes to print the email's pictures too |
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

**2. SSH in** (give it a minute or two to boot): `ssh YOURUSER@jack-in-a-box.local` (or use the Pi's IP from your router's device list).

**3. Paste this one command:**

```bash
curl -fsSL https://raw.githubusercontent.com/jackallen28/jack_in_a_box/main/deploy/bootstrap.sh | bash -s -- --with-ai --reboot
```
That downloads the project to `~/jack_in_a_box`, installs everything, sets it to start on every boot, turns on hourly auto-updates, frees RAM
for the AI and installs Ollama plus the small model (about 10-20 minutes, mostly downloading), then reboots by itself. No account or token is
needed (the repo is public). Leave off `--with-ai --reboot` for a plain install without the AI; add the AI later with
`bash ~/jack_in_a_box/deploy/enable-ai.sh`.

**4. In your browser** (a minute after the reboot): `http://jack-in-a-box.local:8080` -> choose a password -> **Printer** tab (*Check connection*, *Print a test
reminder*) -> **Email** (first box, *Who can email the printer?*: **your own address**; then the printer's Gmail address + app password) -> **Calendar** (iCal link) -> **Messages** (friends, button) ->
**Text & sorting** -> *Test AI summary*, and when it works press **Switch on AI mode**.

The AI is installed but **OFF** until you press that button, so the box works with the built-in summary from the first minute.

## Install on an existing Raspberry Pi OS

One command, run on the Pi (over SSH is fine). It installs everything, sets the app to **start
automatically every time the Pi boots** (and restart itself if it ever crashes), and starts it now:

```bash
curl -fsSL https://raw.githubusercontent.com/jackallen28/jack_in_a_box/main/deploy/bootstrap.sh | bash
```
Re-run it any time to update. It also accepts `-s -- --with-ai` (local AI) and `--reboot`, as in *Full setup* above.

<details><summary>Using a private fork? (needs a read-only token)</summary>

The one-liner can't download from a private repo. Make a read-only token (GitHub -> Settings -> Developer settings -> Personal access
tokens -> **Fine-grained tokens**: your fork, permission **Contents: Read-only**), then on the Pi paste it **once**:

```bash
read -rsp "Paste GitHub token: " T; echo
git clone "https://YOURNAME:${T}@github.com/YOURNAME/YOUR_FORK.git" jack_in_a_box && unset T
bash jack_in_a_box/deploy/install.sh
```
(`read -s` keeps the token out of your shell history.) The installer moves the token into a private file on the Pi
(`data/.git-credentials`, readable only by you) and removes it from the clone's URL. Fine-grained tokens expire: if updates start failing with
"Couldn't reach GitHub", make a new one and re-run the installer, which will ask for it. The public repo needs none of this.
</details>

## Automatic updates

Push to the `main` branch on GitHub and the Pi picks it up by itself, within about an hour:

1. A systemd timer checks GitHub hourly (and 3 minutes after boot).
2. If there are new commits it switches to them, reinstalls Python packages **only if** `requirements.txt` changed, and restarts the app.
3. It then waits for the web page to answer. **If it doesn't, it rolls back to the previous version** and won't retry that
   commit until you push a newer one.
4. Your settings and history (`data/`) are never touched.

See the result on the **Account** tab (current version, last check, *Check & update now*), or switch auto-update off there.
It runs as your normal user; its only extra permission is a sudo rule for exactly two commands (restart this service, start the
updater). Updates need the git-clone install above, not the ZIP route.
`journalctl -u jack-in-a-box-update -n 30` shows what it did.

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
  section order, text size, **white space above and below the printout (mm)**, upload a logo/graphic, footers, labels (DUE / WHEN / ACTIONS), and
  more. *Duplicate* a template to make extra looks (e.g. one for a client).
* **Calendar** - paste your calendar's iCal URL (Google Calendar → Settings → *Integrate calendar* →
  *Public address in iCal format*, or the *Secret address* for private calendars). Each calendar can
  use its own template (title, picture, layout). The *Upcoming* table shows exactly when each event will print.
* **Text & sorting** - date order (4/11), 12/24 h clock, print order, words never to "correct", urgent keywords.
* **Rules** - "if sender contains `@bigclient.com` → use the Client template, title CLIENT, 2 copies"; tag a boss's
  emails `[BOSS]` and mark them urgent; silence noise. Work for calendar events too.
* **Integrations** - an HTTP endpoint so Zapier / Make / Home Assistant / iOS Shortcuts / your own scripts
  can print (`POST /api/print`; see *iPhone Shortcuts* below), a webhook after each print, and a `plugins/` folder for your own Python.
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

## Print from a Google Form

A Google Form on your phone becomes a way to send text, symbols and a picture to the printer. Every submission is a row in a Google Sheet; the box
checks it every minute, **prints each new row once, then deletes the row after it has actually printed** (a row waiting for the printer or the button stays
in the sheet until it prints). Set it up on the **Google Form** tab, which has every step and a copy button for each piece. In short:

1. **Make the form** with these question titles: `Message or reminder?` (dropdown *Reminder* / *Message*), `Symbol 1`, `Symbol 2`, `Symbol 3` (dropdowns; the tab gives
   you the list of symbol names to paste), `Heading` (short answer), `Body` (paragraph), `Image` (file upload), and optionally `From name` (short answer), `Schedule Send (DATE)` (a *Date* question) and `Schedule Send (TIME)` (a *Time* question). Link the form to a Sheet (collecting verified email addresses is optional).
2. **Paste the script** the tab generates into the sheet (Extensions -> Apps Script), then Deploy -> Web app -> *Execute as: Me, Who has access: Anyone*.
3. **Paste the Web app URL** into the tab, switch it on, press *Test connection*.

How a row prints:
* **Reminder** rows go through the same tidy-up as an email (spelling, split into tasks, dates become a DUE line, `[tags]` in the text work). **Message** rows print as written
  under a `MESSAGE` title. Symbol 1-3 print above the heading; a photo makes it one printout with the picture under the text.
* **Everything from your form is treated as you (admin):** it prints straight away and **never waits for the button**, and the *Message or reminder?* choice is honoured.
  If you share the form and want other people's entries handled like friends' emails instead, change *Who are form submissions treated as?* to
  *Decide by the sender's verified email* (and turn on *Collect email addresses: Verified* in the form): your own address prints straight away, a friend always gets a MESSAGE
  and waits for the button unless you ticked *Skip the queue*, and anyone else follows the *not on your lists* setting (hold / print / ignore).
* **From name** (optional). Who the message is from. On a **Message** it replaces the usual name in the title ("from Jack"); on a **Reminder** it adds a small
  "From Jack" line at the bottom. Leave it blank and nothing changes: the sender's usual name is used (your friend's name in *decide by sender* mode). Any header with
  "from", "sender" or just "name" in it is recognised. No new script is needed to use it.
* **Messages show when they were sent.** Every MESSAGE slip (from the form, or a friend's email) has a line at the bottom such as `Sent Tue 3 Nov, 10:15 AM` (your
  timezone, your 12/24-hour setting), printed even if the template's footer is off. For a form message with *Schedule Send* it is the schedule time
  (`Sent Thu 5 Nov, 5:30 PM`), unless that time had already passed, in which case it is when the form was sent. Friends' emails use the email's own date.
* **Schedule Send.** Fill in the two optional questions to print later. Both blank = print straight away. **Date + time** = print at that moment (a moment that has
  already passed, or is right now, prints straight away). **Time only** = that time on the day the form was sent. **Date only** = that morning at the calendar print time
  (7:00 by default); today's date alone prints now.
  **The moment the box sees the row it goes into the queue**, already drawn and stamped *"Prints Thu 05 Nov 17:30"*. It shows on the home page's list (dashed
  "scheduled" badge) and on the Google Form tab under *Waiting in the queue*, each with **Print now** and **Cancel** buttons. At the due time it joins the print queue by itself,
  and it still waits for the printer if that's off. The Google Sheet row is only deleted after it has printed. Times are read in the spreadsheet's timezone
  (File -> Settings in the sheet; or set *Timezone of the Schedule Send answers*). Scheduled printouts say "scheduled for ..." in the history notes.
  **Test connection** lists how each waiting row's schedule was read ("prints Thu 05 Nov 17:30") and warns if the script in your sheet is out of date.
  **Updating an existing setup needs the new script:** open the tab, *Show my script*, paste it over the old one in Apps Script, Save, then
  *Deploy -> Manage deployments -> pencil -> Version: New version -> Deploy*. The Web app URL and secret code don't change.
* A bad or empty row never blocks the ones behind it; a row that couldn't print is left in the sheet for you to see.

No Google password or key is stored on the Pi: only the script's web address and a secret code that the script checks on every request. Google only lets people who are signed in
upload files, and uploaded pictures stay in your Google Drive (the box deletes the sheet row, not the Drive file). I could test the Pi side and the script's logic, but not
Google Forms itself, so expect to need one pass of "does Google's screen look like step 3 says".

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
SUMMARY                    <- an OVERVIEW of the WHOLE email and its history, as facts only (never repeats what's above)
  • Dave Smith says invoice 4471 ($12,480) is 20 days overdue.
  • Jack replied that it was paid on Tuesday.
ACTIONS                    <- everything to DO: your own notes first, then what the email asks of you
[ ] Ring Dave about the credit
[ ] Reply to Dave by Friday
```
* **`[reply by friday]`, `[paper due monday]`, `[cost]`, `[bank details]` ...** anything in square brackets that isn't a symbol is a
  *question*. The box finds the sentences that best match it (words and sense: *reply* also finds "let me know", "get back to", "respond";
  *cost* finds dollar amounts). It only ever quotes the email, and if it isn't mentioned it prints **"Not mentioned in this email."** so
  you know it wasn't missed. `[actions]` and `[deadlines]` pull out the two strongest requests / dates.
* **The summary is bullets, not quoted sentences.** The box reads the whole forward, every message in the conversation, newest first, and
  writes a few short bullets of *overview*: what it is about and the important facts (amounts, dates, places). Anything that has to be *done* (with its deadline)
  goes under **ACTIONS** instead, after your own notes, and a line that says the same thing as another is dropped, so the two lists never overlap. It never prints "no action needed" or "no need to reply": you decide that when you read it.
  The built-in summary does this by rewording and trimming the key sentences; the AI (below) writes them in its own words.
* **`include image`** (or `[image]`, `add the photo`, `attach pictures` ...) anywhere in your notes prints the pictures that came with
  the forwarded email, under the summary. Without it, no pictures print. Signature logos and tiny tracking pixels are never printed. The
  phrase itself is removed from your ACTIONS list. If you ask and the email has no usable picture, the history note says so.
* **`[images attached]`** (also `[all images]`, `[attached images]`, "include all images") prints **every** picture in the forwarded email, including small ones
  and inline ones, not just the "real" attachments. The only pictures it leaves out are ones it **knows** are a signature: those sitting after a sign-off
  such as "Kind regards,", "Thanks, Dave" or "Sent from my iPhone" in the same message (older messages in the thread are judged the same way), and invisible
  1-pixel trackers. A picture it can't place (no sign-off nearby) is kept. Identical copies are printed once. The history note says what was printed and what
  was left out ("left out 3 picture(s) it knew were in a signature at the bottom"). Up to 8 pictures (*Max pictures for [images attached]*).
* **`[full]`** prints the whole message instead of a summary (capped at 2,500 characters, adjustable).
* **Symbols still work** in the same notes: `[work] [!] [reply by friday]`.
* **Signatures never print.** Before anything is summarised or printed, the box removes sign-offs and contact blocks ("Kind regards, Dave /
  Accounts Manager / T: 02 ..."), "Sent from my iPhone", legal disclaimers, "CAUTION: external email" banners, older replies in the
  chain ("On Monday ... wrote:", Outlook From/Sent blocks), long links (shown as "(link)") and the `[cid:image001.png]` / `[image: Logo]`
  placeholders that signature pictures leave behind. Real text is kept: it only cuts what
  looks like a signature.

## Local AI summaries (Raspberry Pi 3B+)

Optional, and the built-in summary is already decent. The AI writes the bullet-point summary of the whole email; `[focus]` and `[full]` never use it.
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
**This is the biggest accuracy upgrade there is:** a Mac or PC can run a 3-8 billion parameter model (`ollama pull qwen2.5:3b`, or `llama3.1:8b`)
in seconds, and those are far better at understanding a whole email thread than the 0.5B model that fits on the Pi. Set *AI: model* to match,
and raise *AI: worked examples shown first* to 3 or 4.

### Symbols picked for you (only when it's sure)
A forwarded-email printout gets **1 to 3 hand-drawn symbols** (Money, Home, Health, Holiday, the work briefcase ...) when they clearly fit, and **none when there's
any doubt** (*Text & sorting -> Pick 1-3 symbols for forwarded emails*). Each symbol has a profile of the emails it belongs on, built from what those emails look
like: **who sends them, what the subject says, what the body says, and the tone**.

| Clue | Examples | Weight |
|---|---|---|
| **Subject** (the strongest) | `Payment received`, `Receipt for your order`, `Appointment reminder`, `Tax invoice`, `Flight retimed`, `Excursion permission slip` | one clear subject clue is enough on its own |
| **Sender** | `Northside Primary School`, `Dr Patel Medical Centre`, `Qantas`, `Ray White Real Estate`, `Commonwealth Bank`, `Petbarn` | also enough on its own |
| **Body** | "blood test", "boarding pass", "performance review", "g-code", "give me a call", "bring a plate" | strong phrases 2 points, weak hints 1; needs two clues |
| **Tone** | "Dear Mr Chen", "Please find attached", "do not hesitate to contact", "Pty Ltd", "General Manager" | a clearly **formal / business letter** earns the **briefcase**; casual ("hey", "lol", "cheers mate") counts against |

About 1,500 words and phrases across the 17 symbols; the reasoning for each is written beside it in `app/autosymbols.py` (who sends these emails, what the subject looks
like, what to avoid), so you can read it, tighten it or add your own.
* **Symbols you type always win.** If your note has `[school]` (or any symbol tag), nothing is added.
* **With AI mode on**, the AI is shown only a **shortlist**: the few symbols that have some evidence in *this* email, each with a plain description of the emails it suits and
  typical subjects. It proposes up to 3, or `SYMBOLS: none`. *None* is final, and a proposal is accepted only if the email backs it up, so it can't invent one.
* **Without the AI** (or if it didn't follow the format), the clues alone must be convincing: 4+ points and a subject/sender/tone anchor or two different clues.
* Overlapping clues aren't counted twice. A weaker symbol is kept if it's nearly as well supported *or* has its own clear subject clue (a "Receipt" subject keeps Money
  on a shop's order email). At most 3. The abstract symbols (!, ?, star, idea, email, clock, done) are never picked automatically.
* Your own custom symbols (name and tags) and any extra tag words you added to a built-in symbol count as subject-level clues.

History notes say why: `symbols chosen: Money (subject 'payment received'), Work (formal tone)`, or "symbols left out" when the AI guessed something the email didn't back up.
On the **AI review** tab you can correct the symbols for each summary, and your corrections are shown to the model as part of the worked examples.
`python -m app.evaluate` also reports **symbols safe**: the share of the 10 test emails where nothing unacceptable was chosen.

### AI review: keep the history, correct it, feed it back
Open the **AI review** tab. Every forwarded email the box summarises is kept there (in `data/` on the Pi only, never uploaded or committed), next to its
printout, what the AI was shown, and what it wrote (built-in summaries are kept too, marked as such). For each one you can:

* **It's good**: the summary is used as a worked example as it is.
* **Save my version**: edit the bullets (one per line; start a line with `ACTION:` for a to-do) and save. Your version becomes the worked example.
* **Bad, no replacement**: noted, never used as an example.
* **Comment + "make my comment a standing rule"**: e.g. *Always say who the email is from*. Rules (up to 8, also editable on the tab) are added to the AI's
  instructions on every summary.

What that does to the next summary: your good and corrected summaries are shown to the model as worked examples **ahead of the built-in 10**, picked by how
similar the email is (setting *AI: learn from my reviewed summaries*; *AI: worked examples shown first* sets how many). That, plus your rules, is what
changes its behaviour. A 1 GB Pi cannot retrain the model itself, so for real fine-tuning press **Download training data**: a `.jsonl` file of
system / email / your-answer chat records, ready for a fine-tuning tool on a Mac or PC.

Check whether your corrections are making it better (each corrected email is hidden from the model while it is tested, so it can't just copy):
```bash
cd ~/jack_in_a_box && venv/bin/python -m app.evaluate --mine --show
```
Unreviewed history is deleted after *AI: keep that history for (days)* (default 90, at most 500 kept); good and corrected ones stay. Turn the history off with
*AI: keep a history of summarised emails for review*, or press **Delete unreviewed history** any time.

### "Training" the AI (it can't be retrained on a Pi, but it can be taught by example)
Real training needs a GPU and gigabytes of RAM; a 1 GB Pi can't do it. What works, and what this box does instead:

* **Worked examples.** `app/summary_examples.json` holds **10 training emails**, each with the bullets a good summary would have (invoice
  chase, school notice, appointment, newsletter, interview, flight change, lease renewal, tradie quote, decision thread, invitation).
  Before your real email, the box shows the model the closest one(s) as a worked example (by shared words), which makes a small model copy the
  *style*: short bullets, facts first, `ACTION:` with a deadline. *AI: worked examples shown first* sets how many (default 1; each one adds
  a few seconds on a Pi). Add your own: copy an entry, paste in a real email (remove anything private) and write the bullets you wish it
  had printed. Every bullet must only use facts in its email.
* **A test set.** The same file has an `eval` list of emails the model is **never shown**, so you can measure honestly:

```bash
cd ~/jack_in_a_box
venv/bin/python -m app.evaluate --show                       # your Pi's Ollama and model, 10 emails (about a minute each on a 3B+)
venv/bin/python -m app.evaluate --builtin                    # the no-AI summary, for comparison
venv/bin/python -m app.evaluate --url http://192.168.1.20:11434 --model qwen2.5:3b --examples 3    # your Mac
```
  For each email it checks: did the AI's answer pass the not-invented check, a sensible number of bullets, every key fact (amount, date,
  place) present, and a to-do present exactly when the email asks for something. Change a setting, run it again, keep what scores better.

## AirPrint: print to the receipt printer from your other devices

Tick **AirPrint -> Share the printer on my network** (and Save). The printer then shows up in the print dialog of iPhones, iPads, Macs, Windows 10/11 PCs and
Android devices on your network, and you print to it like any printer plugged into them. There is **no CUPS and no driver to install**: the box speaks IPP itself and
announces the printer over Bonjour (mDNS).

* **What you can print:** PDFs, photos (JPEG/PNG), web pages and documents (devices turn those into PDF or Apple/PWG raster, which it reads), and plain text.
* **How it comes out:** each page is trimmed of blank paper (top and bottom, and equal side margins; *Trim blank paper* in the settings), scaled to the paper width
  (576 dots / 72 mm), thresholded for text and line art or dithered for photos (decided per page, or forced in the settings), and put on the **same print queue** as emails
  and form messages. A blank page prints nothing. It appears in the history as an `airprint` printout you can Cancel or Reprint; the device's print status (printing,
  done, cancelled) follows the queue. A page is scaled to the paper, so choose a small paper size (4x6 in or A6) on the phone for readable text.
* **Limits:** at most 10 pages and 5 copies per document, 30 MB per document, and "busy" when 20 pages are already waiting (all adjustable). *Hold AirPrint pages until I press the
  message button* makes them wait for the button like friends' messages.
* **Who can print:** only devices on your own network (private addresses). Turning that off lets anything that can reach the Pi print. It is plain IPP (not encrypted), fine on a home network.
* **Finding it:** it listens on port 631 (or 6310 if 631 isn't available, which the status page tells you) and announces the name you chose. If a device doesn't list it:
  iPhone/Mac use *Add Printer -> IP* with the address shown on the AirPrint page and queue `ipp/print`; Windows: *Add manually -> Select a shared printer by name* ->
  `http://<the Pi's address>:631/ipp/print`. The **Test it** button asks the printer for its description, the way a phone would.
* **To install:** the update installs two small packages (`zeroconf` for Bonjour, `pypdfium2` for PDFs). Without `zeroconf` it still works when you add it by address; without
  `pypdfium2` PDFs are refused (the page says so).
* **Honest caveat:** I built and tested this against an independent IPP library and real multicast announcements on the same machine, and with raster files I generated, but
  **not with a real iPhone, Mac, Windows PC or Android phone**. If a device can't find it or prints something odd, tell me which device and what you saw.

### Using the printer's own Epson driver on a Mac or Windows PC ("prints exactly as if it were plugged in")
AirPrint redraws your document for the receipt paper. If you'd rather the computer use **Epson's own driver** (its fonts, paper sizes, cutter and receipt layout), tick
**AirPrint -> Allow the printer's own Epson driver** and Save. The computer's driver then makes the printer data itself, and this box passes it **straight through,
byte for byte** to the printer (over the cable you already have to it, USB or Ethernet). It goes through the same print queue (history, Cancel, Reprint, ordering, retry if the printer is off),
and a lock makes sure it never mixes with another printout.

* **Mac:** install Epson's driver for the TM-T82II (Epson's support site, search "TM-T82II", choose macOS; newer macOS versions use Epson's "TM Series Printer Driver for Mac", and I
  couldn't confirm from here which package covers the TM-T82II on your macOS, so check the supported-models list). Then *System Settings -> Printers & Scanners -> Add Printer (+) -> IP tab*:
  Address = the Pi's address (`pi.local`), Protocol **Internet Printing Protocol - IPP**, Queue `ipp/print` (or Protocol **HP Jetdirect - Socket**, which uses the raw port). *Use:* **Select Software...**
  and pick the **EPSON TM-T82II** driver (not "AirPrint"). Set the paper size and cutter in the printer's Options.
* **Windows:** install Epson's driver, add a printer with a *Standard TCP/IP port* to the Pi's address, port **9100**, raw.
* **How it works:** over IPP the Mac sends the driver's data as `application/vnd.cups-raw` (or octet-stream); on the raw port (9100) a job is whatever arrives until the sender closes or goes quiet for 4 seconds.
  Only when this option is on are those accepted; documents (PDF, photos...) are still drawn by the box as before.
* **Limits and risks:** raw printer data can do anything the printer's command language can (including changing its settings), so this is **off by default** and, like AirPrint, only
  accepts computers on your own network. Epson's status monitor can't see paper-out or cover-open through this box (it only forwards the data), and a driver that insists on two-way
  status may complain. I couldn't test with a real Mac and Epson's driver, only with simulated drivers sending ESC/POS data, so tell me what you see.

## iPhone Shortcuts: send a phrase, or a shared screenshot, to the printer

The **iPhone Shortcuts** tab switches on the print API, shows your web address and secret token (with Copy buttons), and has step-by-step recipes, with the Apple Intelligence
prompts ready to paste, for two shortcuts:

1. **"Remind the printer"**: you type or dictate a phrase (also "Hey Siri, remind the printer", or the Action Button). A *Use Model* step (Apple Intelligence, on-device or Private Cloud
   Compute) turns it into `{heading, text, symbols}`, and a *Get Contents of URL* step posts it to the box. The box already tidies spelling, splits several tasks and turns "tomorrow 5pm" into a DUE
   line, so the AI step is optional (without it, just send the phrase as `text`).
2. **"Print this screenshot"**: it appears in the **Share Sheet** for images (screenshot preview -> Share, or Photos -> Share). *Extract Text from Image* reads the screenshot on the phone, *Use Model*
   decides what the reminder or message is, and the shortcut posts it (and, if you like, the screenshot itself, which prints under the reminder). Apple's own models read text rather than pictures, so this
   goes via the text in the image. If you want the AI to look at the picture itself (a photo, handwriting), choose *ChatGPT* as the model in *Use Model*; that sends the image to OpenAI.

The API is `POST /api/print` with the header `Authorization: Bearer <token>`; send JSON or a form (a form can include picture files):

| Field | Meaning |
|---|---|
| `text` | the reminder or message (spelling fixed, several tasks split, dates -> DUE) |
| `heading` | optional bold heading |
| `kind` | `reminder` (default), `message` (a MESSAGE slip showing when it was sent) or `plain` (printed exactly as written) |
| `from` | optional "from" name: a message says "from Jack"; a reminder gets a "From Jack" line |
| `symbols` | optional, names or numbers: `school, money` (unknown ones are reported back, not fatal) |
| `images` | picture file(s) in a form (also `image`/`file`), or `image_base64` / `images_base64` in JSON: up to 4, printed under the text; screenshots, photos and HEIC |
| `urgent`, `copies`, `template` | as on the Print page (copies at most 5) |

The answer is JSON such as `{"ok": true, "message": "Sent 1 printout to the printer"}`; mistakes get a clear `error` (wrong token = 401, nothing to print = 400, queue full = 429, over 20 MB = 413).
The phone must be on your home Wi-Fi (or a VPN such as Tailscale when you're out); the first run asks to let Shortcuts find devices on the local network. I couldn't run Shortcuts here, so
the recipes follow how it works but action names can differ between iOS versions; tell me if a step doesn't match what you see.

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
* Summaries, `[focus]` and `[full]` only read the email text, not attachments (PDF/Word) or the words inside pictures. The built-in summary trims and rewords sentences from the email; only the AI writes fresh wording.
* `[focus]` matching is word-and-synonym based, so an unusual phrasing can be missed (it then says "Not mentioned"). Tell me which ones and I'll add synonyms.
* Local AI, the fan, GPIO and the installers were tested against simulated hardware (a fake Ollama server, mock GPIO pins, a local git remote), not a real Pi.
* HEIC photos need `pip install pillow-heif`.
