"""Pick 1-3 hand-drawn symbols for a forwarded-email printout - but only when it is clearly right.

HOW IT DECIDES
Every symbol has a profile: what kind of email it belongs on (``about``) and the clues that give it away, in four places
  * SUBJECT    the subject line is the strongest signal ("Receipt for your order", "Appointment reminder"): one clear
               subject clue is enough on its own
  * SENDER     who it is from ("Northside Primary School", "Qantas", "Dr Patel Medical Centre", "...Pty Ltd")
  * BODY       phrases in the message: strong ones are specific ("blood test", "boarding pass"), weak ones only hint
  * TONE       a formal / business letter ("Dear Mr Smith", "Please find attached", "Kind regards", "Pty Ltd") supports the
               work briefcase even when no single keyword does
Points: subject clue 4 (2 if weak) - sender clue 3 - body strong 2 / weak 1 - strong formal tone 4 / mild 2.

THE RULES (unsure = no symbol)
  * with no AI answer: a symbol needs 4+ points AND either one "anchor" (a subject clue or strong tone) or two different clues
  * with the AI on: only symbols that have SOME evidence in this email are offered to it (a shortlist, each with a plain
    description of the emails it suits); it may propose up to 3 or write "none".  "none" is final, and a proposal is only
    accepted if the email scores 2+ for it, so the AI can never invent a symbol
  * at most 3; without the AI, a second or third symbol must score at least 60% of the best, or have its own clear subject clue
  * abstract symbols (!, ?, star, idea, email, clock, done) are never chosen automatically
  * symbols you type yourself always win (nothing is added)

Plain-word syntax in the lists below: whole words, any case; "blood test" also matches "blood-test"; a trailing * is any ending
(celebrat* = celebrate, celebrating); most words also match their plural; "re:..." is a raw regular expression.
"""
from __future__ import annotations

import re

from . import icons as icon_lib

NEVER_AUTO = {"exclamation", "question", "star", "idea", "email", "clock", "tick"}
SUBJECT_STRONG, SUBJECT_WEAK, SENDER, BODY_STRONG, BODY_WEAK, TONE_STRONG, TONE_MILD = 4, 1, 4, 2, 1, 4, 2
SOLO_SCORE, SOLO_TERMS = 4, 2          # no AI answer: points needed, and how many different clues if there is no anchor
AI_SCORE = 2                           # the AI proposed it: the email must still back it up by this much
MAX_SYMBOLS, RELATIVE, SHORTLIST = 3, 0.6, 6

# --------------------------------------------------------------------------------------------------------------------
# The profiles.  Reasoning for each: WHO sends these emails, what the SUBJECT looks like, what the BODY says, what to avoid.
# --------------------------------------------------------------------------------------------------------------------
SPECS: dict[str, dict] = {
    # Briefcase. Employers, colleagues, clients, suppliers, recruiters; any formal business correspondence. Subjects name a
    # meeting, project, contract or role. Senders are companies (Pty Ltd, Group, Consulting, Recruitment). Tone is formal.
    # Avoid: bare "work" (home work, work on the deck) - phrases only.
    "work": {
        "about": "business and professional emails: colleagues, clients, meetings, projects, contracts, jobs and formal letters",
        "subject": ["meeting", "team meeting", "client meeting", "minutes", "proposal", "contract", "agreement", "tender", "interview", "job offer",
                    "offer of employment", "vacancy", "your application", "timesheet", "roster", "shift", "project update",
                    "project", "quote request", "quotation", "purchase order", "performance review", "annual leave", "leave request", "onboarding",
                    "welcome to the team", "re: proposal", "presentation", "conference",
                    "workshop", "webinar", "briefing", "all staff"],
        "sender": ["consulting", "consultants", "recruitment",
                   "recruiter", "careers", "human resources", "hr", "legal", "lawyers", "solicitors", "linkedin", "seek"],
        "strong": ["colleague", "client", "stakeholder", "supervisor", "boss", "employer", "employee", "workplace", "office", "board meeting",
                   "staff meeting", "team meeting", "stand-up", "action items", "next steps", "deliverable", "key performance", "kpi", "business case",
                   "per our conversation", "as discussed", "further to our", "with reference to", "please find attached", "please find enclosed",
                   "i am writing to", "we are writing to", "on behalf of", "head office", "annual report", "quarterly", "budget meeting", "job application",
                   "cover letter", "resume", "salary", "promotion", "onboarding", "human resources", "work from home", "site visit", "work order",
                   "statement of work", "scope of work", "terms and conditions", "non-disclosure", "nda", "purchase order", "supplier", "procurement"],
        "weak": ["report", "presentation", "contract", "staff", "team", "order", "quote", "candidate", "application", "follow up", "manager",
                 "director", "department", "company", "business"],
        "tone": "formal",
    },
    # Graduation cap. Schools, childcare, universities, tutors. Senders have "School/College/Academy/University/TAFE/Kindergarten/PTA";
    # subjects: excursion, permission slip, newsletter, term dates, fees, enrolment. Body speaks to parents about "your child".
    "school": {
        "about": "school, childcare and university emails: teachers, excursions, homework, term dates, fees, parent notices",
        "subject": ["school", "excursion", "permission", "permission slip", "newsletter term", "term dates", "year \\d+", "parent teacher",
                    "parent-teacher", "enrolment", "enrol*", "uniform", "school fees", "fees due", "homework", "exam*", "timetable",
                    "report card", "school report", "carnival", "sports day", "assembly", "pupil free day", "lecture", "tutorial", "semester", "graduation", "orientation", "kinder", "kindergarten", "childcare", "daycare", "p&c", "pta",
                    "canteen", "lunch order", "school bus", "school photos"],
        "sender": ["school", "primary", "high school", "secondary college", "college", "academy", "university", "uni", "tafe", "kindergarten", "kinder",
                   "preschool", "childcare", "child care", "daycare", "early learning", "education", "edu", "p&c", "pta", "tutoring", "tutor", "canvas",
                   "seesaw", "compass", "schoolzine", "skoolbag"],
        "strong": ["dear parents", "dear families", "your child", "your son", "your daughter", "students", "teacher", "principal", "headmaster",
                   "classroom", "homework", "excursion", "school camp", "school uniform", "school fees", "term dates", "pupil free day", "parent teacher",
                   "parent-teacher interview", "report card", "swimming carnival", "sports carnival", "school assembly", "permission slip", "year level",
                   "re:year \\d+", "kindergarten", "preschool", "enrolment", "lecture", "tutorial", "semester", "exam", "assignment", "curriculum",
                   "learning support", "school bus", "canteen"],
        "weak": ["class", "term", "parents", "grade", "learning", "study", "camp", "reading", "tutor", "kids", "children", "teaching"],
    },
    # Party face. Social invitations, events, tickets, entertainment, fundraisers. Senders: Eventbrite, Ticketek, Meetup, Facebook events.
    # Subjects: "You're invited", RSVP, party, BBQ, drinks. Body: "come along", "bring a plate". Light, friendly tone.
    "fun": {
        "about": "social invitations and entertainment: parties, events, tickets, dinners, get-togethers, fundraisers",
        "subject": ["invited", "you're invited", "invitation", "rsvp", "party", "bbq", "barbecue", "drinks", "dinner", "lunch", "brunch", "picnic",
                    "catch up", "get together", "get-together", "reunion", "celebration", "celebrate", "tickets", "gig", "concert", "festival", "movie night", "game night", "games night", "trivia", "karaoke", "fundraiser", "night out", "sleepover", "playdate",
                    "christmas party", "end of year", "street party", "come along", "join us", "fun day", "family day"],
        "sender": ["eventbrite", "ticketek", "ticketmaster", "moshtix", "oztix", "humanitix", "meetup", "events", "entertainment", "cinema", "theatre",
                   "event cinemas", "hoyts", "village cinemas", "festival", "tickets"],
        "strong": ["you are invited", "you're invited", "please join us", "join us for", "come along", "bring a plate", "byo", "rsvp", "party",
                   "celebrat*", "concert", "festival", "picnic", "game night", "movie night", "pizza night", "barbecue", "bbq", "fundraiser", "sleepover",
                   "playdate", "day out", "gig", "tickets", "theatre", "sports day", "christmas party", "end of year party", "social club", "trivia night",
                   "karaoke", "camping", "street party", "block party", "garden show", "get together", "catch up"],
        "weak": ["invited", "invitation", "dinner", "drinks", "fun", "event", "community", "lunch", "brunch", "games", "music", "friends", "weekend"],
    },
    # Palm/suitcase. Trips and bookings. Senders: airlines, Booking.com, Airbnb, Expedia, Flight Centre, hotels, tour operators.
    # Subjects: itinerary, e-ticket, booking confirmation, check-in, boarding pass. Body: times, terminals, baggage.
    "holiday": {
        "about": "travel emails: flights, hotels, bookings, itineraries, tours, trips and time off",
        "subject": ["flight", "flights", "itinerary", "e-ticket", "eticket", "boarding pass", "check-in", "check in", "online check-in", "booking confirmation",
                    "booking confirmed", "your stay", "your trip", "trip", "travel", "holiday", "holidays", "vacation", "getaway", "cruise",
                    "hotel", "resort", "accommodation", "airbnb", "car hire", "hire car", "rental car", "passport", "departure", "retimed",
                    "schedule change", "gate change", "long weekend", "school holidays", "travel insurance"],
        "sender": ["qantas", "virgin", "jetstar", "rex", "tigerair", "emirates", "singapore airlines", "air new zealand", "airline", "airlines", "airways",
                   "airport", "booking.com", "airbnb", "expedia", "webjet", "flight centre", "wotif", "trivago", "hotels", "hotel", "resort", "travel",
                   "tours", "cruise", "holidays", "hertz", "avis", "budget car", "europcar", "stayz", "trip.com", "lastminute"],
        "strong": ["boarding pass", "itinerary", "e-ticket", "flight", "airline", "airport", "check-in opens", "online check-in",
                   "baggage allowance", "carry-on", "checked baggage", "hotel", "resort", "cruise", "passport", "visa application", "travel insurance",
                   "car hire", "hire car", "rental car", "booking reference", "booking confirmation", "your booking", "reservation", "your stay",
                   "check-out", "check out", "accommodation", "getaway", "long weekend", "school holidays", "public holiday", "overseas", "holiday",
                   "vacation", "departure", "layover", "frequent flyer", "boarding gate", "airport terminal"],
        "weak": ["travel", "trip", "booking", "luggage", "arrive", "depart*", "destination", "flying", "check-in", "tour", "stay", "beach"],
    },
    # Calendar page. Anything where a date/time was set or changed: appointments, invites, reminders, rescheduling.
    # Senders: calendar tools (Google Calendar, Outlook, Calendly, Acuity). Subjects: Reminder, Appointment, Rescheduled, Invitation:.
    "calendar": {
        "about": "scheduling emails: appointments, calendar invites, reminders, rescheduled or moved times, dates to remember",
        "subject": ["appointment", "your appointment", "appointment reminder", "reschedul*", "invitation:", "updated invitation",
                    "accepted:", "declined:", "calendar", "save the date", "agenda", "date and time", "new time", "time change", "changed time",
                    "booking confirmed", "timetable", "meeting invite", "event details",
                    "important dates", "term dates", "rsvp by", "retimed", "see you on", "see you at"],
        "sender": ["calendar", "google calendar", "calendly", "acuity", "simplybook", "booksy", "timetap", "appointments", "bookings", "reminders"],
        "strong": ["appointment", "reschedul*", "save the date", "agenda", "calendar", "calendar invite", "meeting invite", "date and time", "your appointment",
                   "see you on", "important dates", "timetable", "schedule change", "event details", "time change", "new time", "has been moved",
                   "has been rescheduled", "moved to", "retimed", "starts at", "venue and time", "session time", "confirm your attendance", "rsvp by",
                   "add to calendar", "reminder that"],
        "weak": ["booking", "session", "deadline", "closes", "opens", "reminder", "due date", "date", "attend*", "time", "arrive"],
    },
    # 3D-printer. Hobbyist/maker emails. Senders: Prusa, Bambu Lab, Creality, Printables, Thingiverse, filament shops.
    # Subjects: filament, your print, STL, order for a printer part. Body: nozzle, layer height, infill.
    "3dprinter": {
        "about": "3D printing: filament, printers, STL / G-code files, prints, slicers, maker and prototype emails",
        "subject": ["3d print*", "3d printer", "filament", "stl", "gcode", "g-code", "print farm", "printables", "thingiverse", "prusa", "bambu", "creality",
                    "elegoo", "anycubic", "slicer", "your print", "resin", "pla", "petg", "nozzle", "hotend", "maker"],
        "sender": ["prusa", "bambu", "bambu lab", "creality", "elegoo", "anycubic", "printables", "thingiverse", "matterhackers", "3djake", "polymaker",
                   "filament", "3d", "makerbot", "ultimaker", "maker"],
        "strong": ["3d print*", "3d printer", "filament", "gcode", "g-code", "stl", "stl file", "print farm", "nozzle", "hotend", "build plate", "print bed",
                   "prusa", "bambu", "creality", "ender 3", "pla", "petg", "slicer", "cura", "resin printer", "additive manufacturing", "printed part",
                   "print time", "layer height", "infill", "bed levelling", "bed leveling", "print job", "first layer"],
        "weak": ["print", "resin", "spool", "maker", "prototype", "cad", "printer", "model", "design file"],
    },
    # Calculator. Tax, invoices and statements between a business and its accountant/clients. Senders: ATO, Xero, MYOB, accounting firms.
    # Subjects: Invoice, Tax invoice, Statement, BAS, GST, Remittance, Overdue, Payroll. Formal, numbers, due dates.
    "accountant": {
        "about": "accounting and tax emails: invoices, statements, BAS/GST, payroll, remittances, overdue accounts, bookkeeping",
        "subject": ["invoice", "tax invoice", "overdue invoice", "invoice overdue", "statement", "statement of account", "remittance", "remittance advice",
                    "payment advice", "bas", "gst", "tax", "tax return", "payg", "payroll", "pay slip", "payslip", "superannuation", "overdue",
                    "accounts payable", "accounts receivable", "bookkeeping", "financial year", "eofy", "audit", "ledger", "credit note",
                    "purchase order", "depreciation", "ato"],
        "sender": ["accountant", "accountants", "accounting", "accounts", "bookkeeping", "bookkeeper", "tax", "ato", "australian taxation office",
                   "xero", "myob", "quickbooks", "h&r block", "revenue", "finance", "billing", "payroll", "audit", "cpa", "chartered"],
        "strong": ["invoice", "tax invoice", "overdue invoice", "accountant", "tax", "tax return", "gst", "bas", "bas statement", "ato", "bookkeeper",
                   "bookkeeping", "payroll", "receipt", "audit*", "ledger", "remittance", "remittance advice", "accounts payable", "accounts receivable",
                   "profit and loss", "p&l", "balance sheet", "financial year", "end of financial year", "eofy", "superannuation", "depreciation", "xero",
                   "myob", "quickbooks", "payment advice", "credit note", "statement of account", "lodgement", "lodge your", "chart of accounts",
                   "payment terms", "net 30", "amount owing", "outstanding invoice"],
        "weak": ["payment", "overdue", "balance", "statement", "bill", "premium", "refund", "expenses", "account", "fees", "due"],
        "tone": "formal",
    },
    # Notebook. Admin, paperwork and record-keeping tasks. Subjects: Timesheet, Expense claim, Forms, Reconciliation, Documents needed.
    "bookwork": {
        "about": "admin and record-keeping: paperwork, forms, timesheets, expense claims, reconciliations, filing",
        "subject": ["timesheet", "timesheets", "expense claim", "expenses", "expense report", "mileage", "logbook", "reconciliation", "paperwork", "forms",
                    "form", "documents required", "documents needed", "please complete", "admin", "records", "filing", "receipts needed", "data entry",
                    "monthly figures", "end of month", "bookwork", "bookkeeping", "spreadsheet", "register", "compliance"],
        "sender": ["bookkeeping", "compliance"],
        "strong": ["bookwork", "reconcil*", "timesheet", "bank reconciliation", "data entry", "bookkeeping", "expense report", "expense claim", "mileage log",
                   "logbook", "paperwork", "admin work", "spreadsheet", "monthly figures", "end of month", "update the books", "filing", "fill in the form",
                   "complete the form", "sign and return", "documents required", "supporting documents", "please complete", "compliance"],
        "weak": ["forms", "form", "admin", "records", "files", "documents", "enter the", "figures", "register", "signed"],
    },
    # House. Property and household: landlords, real-estate agents, tradies, utilities, strata, councils.
    # Senders: Ray White, LJ Hooker, "Realty", "Plumbing", "Energy", "Council". Subjects: lease, rent, inspection, maintenance, repair, quote.
    "home": {
        "about": "house and property emails: lease, rent, landlord, tradespeople, repairs, utilities, council, strata",
        "subject": ["lease", "rent", "rental", "inspection", "maintenance", "repair", "repairs", "renovation", "council rates", "rates notice",
                    "strata", "body corporate", "mortgage", "landlord", "tenant", "tenancy", "real estate", "plumber", "electrician", "builder",
                    "gas bill", "electricity bill", "water bill", "energy bill", "nbn", "removalist", "pest control", "gutters", "roof", "leak", "hot water"],
        "sender": ["real estate", "realty", "ray white", "lj hooker", "property", "properties", "strata", "body corporate", "plumbing", "plumber",
                   "electrical", "electrician", "builder", "maintenance", "council", "energy", "agl", "origin", "energyaustralia",
                   "nbn", "bunnings", "handyman", "roofing", "landscaping", "pest", "home loans"],
        "strong": ["lease", "landlord", "tenant", "rent", "rental", "mortgage", "real estate", "real estate agent", "property manager", "rental inspection",
                   "plumber", "plumbing", "electrician", "renovat*", "maintenance", "repair", "council rates", "body corporate", "strata", "builder",
                   "handyman", "gutter", "leak", "blocked drain", "hot water", "air con*", "pest control", "removalist", "moving house", "back door",
                   "front door", "fence", "tradesperson", "landscap*", "quote for your deck", "settlement", "open for inspection", "bond refund", "rental bond"],
        "weak": ["house", "apartment", "property", "garden", "deck", "door", "kitchen", "roof", "gas", "electricity", "lawn", "bathroom", "unit", "street",
                 "home"],
    },
    # Shopping bag. Online/retail orders and deliveries. Senders: Amazon, eBay, Australia Post, Woolworths, Bunnings, IKEA, couriers.
    # Subjects: Your order, Shipped, Out for delivery, Delivered, Tracking, Parcel, Sale, Voucher.
    "shopping": {
        "about": "shopping emails: orders, parcels and deliveries, tracking, sales, vouchers, purchases from shops and online stores",
        "subject": ["your order", "order confirmation", "order #", "shipped", "your parcel", "parcel", "out for delivery", "delivered", "delivery",
                    "tracking", "dispatched", "your purchase", "checkout", "sale", "voucher", "gift card", "discount", "back in stock", "click and collect", "ready for pickup", "ready to collect", "groceries"],
        "sender": ["amazon", "ebay", "etsy", "temu", "kmart", "target", "big w", "bunnings", "ikea", "woolworths", "coles", "aldi", "officeworks",
                   "jb hi-fi", "the good guys", "harvey norman", "myer", "david jones", "catch", "afterpay", "zip", "australia post", "auspost", "startrack",
                   "dhl", "sendle", "fedex", "ups", "couriers please", "aramex", "shop", "store", "orders", "delivery", "parcel", "dispatch"],
        "strong": ["order", "order confirmation", "deliver*", "parcel", "shipped", "shipping", "tracking", "tracking number", "cart", "checkout", "shopping",
                   "groceries", "supermarket", "click and collect", "pick up your order", "dispatch*", "courier", "australia post", "auspost",
                   "return policy", "gift card", "voucher", "sold out", "out for delivery", "your purchase", "estimated delivery", "delivery address",
                   "your items", "add to cart", "free shipping"],
        "weak": ["sale", "discount", "purchase", "store", "stock", "special offer", "items", "offer", "buy", "price"],
    },
    # Coin. Payments and banking. Senders: banks, PayPal, Stripe, Afterpay, credit unions, Centrelink. Subjects: Receipt, Payment, Refund,
    # Statement, Transaction, Bill, Fee, Balance. The user's rule of thumb: a subject with "receipt" or "payment" is enough.
    "money": {
        "about": "payments and banking: receipts, payments, refunds, bills, fees, balances, transfers, loans, prices and amounts due",
        "subject": ["receipt", "your receipt", "payment", "payment received", "payment due", "payment confirmation", "payment failed", "paid", "refund",
                    "deposit", "transfer", "transaction", "statement", "bill", "billing", "fee", "fees", "charge", "charged", "balance",
                    "direct debit", "loan", "interest", "overdue", "amount due", "total due", "subscription", "premium",
                    "cashback"],
        "sender": ["bank", "commonwealth bank", "cba", "anz", "westpac", "nab", "ing", "bendigo", "suncorp", "credit union", "paypal", "stripe", "square",
                   "afterpay", "zip", "wise", "revolut", "visa", "mastercard", "amex", "centrelink", "services australia", "finance", "payments", "billing",
                   "insurance", "insurer", "superannuation", "loans"],
        "strong": ["receipt", "payment received", "payment due", "payment confirmation", "pay by", "pay now", "refund", "deposit", "loan", "mortgage", "premium",
                   "price", "price rise", "price increase", "amount due", "total due", "outstanding balance", "direct debit", "bank transfer", "bank details",
                   "account number", "bsb", "credit card", "interest rate", "invest*", "savings", "centrelink", "late fee", "overdue", "per week",
                   "per month", "per year", "budget", "statement", "transaction", "charged to your card", "your card"],
        "weak": ["re:\\$\\d[\\d,.]*", "cost", "fee", "payment", "pay", "bank", "credit", "quote", "insurance", "rent", "annual", "charge", "paid", "money"],
    },
    # Handset. Calls and texts. Subjects: Missed call, Voicemail, Please call, Call me. Body: "ring me", "call me on", numbers.
    # Senders: telcos (Telstra, Optus) - their bills are money, but "voicemail/missed call" notices are phone.
    "phone": {
        "about": "phone and call emails: missed calls, voicemail, call-back requests, text messages",
        "subject": ["missed call", "voicemail", "voice message", "please call", "call me", "call back", "callback", "phone call", "text message",
                    "sms", "whatsapp", "ring me", "urgent call"],
        "sender": ["telstra", "optus", "vodafone", "voicemail", "whatsapp", "sms", "mobile", "telecom", "2degrees"],
        "strong": ["phone call", "give me a call", "give us a call", "call me back", "call us", "call you", "ring me", "ring you", "ring back", "voicemail",
                   "missed call", "phone number", "call back", "on the phone", "text message", "sms", "whatsapp", "my number is", "call me on", "call on",
                   "tried to call", "tried calling", "leave a message"],
        "weak": ["call", "mobile", "text me", "contact number", "dial", "ring", "phone"],
    },
    # Cake. Birthdays. Subjects: Birthday, Happy birthday, Birthday party, Turning N. Body: cake, candles, presents, "bring a gift".
    "birthday": {
        "about": "birthday emails: birthday parties, birthday wishes, turning an age, cake and presents",
        "subject": ["birthday", "happy birthday", "birthday party", "bday", "b'day", "birthday invitation", "birthday dinner", "birthday drinks",
                    "birthday celebration", "cake"],
        "sender": ["birthday", "bakery", "cakes", "cake shop", "party hire", "party supplies", "kids party"],
        "strong": ["birthday", "bday", "cake", "re:turning \\d+", "re:\\d+(?:st|nd|rd|th) birthday", "happy birthday", "birthday party", "candles",
                   "birthday present", "birthday girl", "birthday boy", "many happy returns", "blow out the candles", "birthday dinner"],
        "weak": ["present", "gift", "balloons", "surprise", "party", "age"],
    },
    # Heart. Love and care: weddings, anniversaries, engagements, baby news, get-well and condolences. Warm, personal tone.
    "heart": {
        "about": "love and care: weddings, anniversaries, engagements, new babies, get-well wishes, condolences and thanks from the heart",
        "subject": ["wedding", "wedding invitation", "anniversary", "engagement", "engaged", "congratulations", "congrats", "get well", "get well soon",
                    "thinking of you", "condolences", "sympathy", "funeral", "in loving memory", "memorial", "new baby", "baby shower", "valentine*",
                    "with love", "love you", "bridal shower", "hens", "bucks"],
        "sender": ["wedding", "weddings", "funeral", "funerals", "bridal"],
        "strong": ["anniversary", "wedding", "valentine*", "engagement", "condolence", "sympathy", "get well", "get well soon", "thinking of you", "with love",
                   "in loving memory", "funeral", "baby shower", "new baby", "congratulations", "love you", "wedding invitation", "bridal shower",
                   "rest in peace", "our thoughts", "celebration of life", "passed away", "heartfelt"],
        "weak": ["love", "best wishes", "thoughts", "thank you so much", "grateful", "caring", "sorry for your loss"],
    },
    # Medical cross. Doctors, dentists, hospitals, pharmacies, physios, health funds. Senders: "Medical Centre", "Dental", "Pathology", Medicare, Bupa.
    # Subjects: Appointment reminder, Prescription, Test results, Referral. Body: patient, clinic, bring your Medicare card.
    "health": {
        "about": "health emails: doctor, dentist and hospital appointments, prescriptions, test results, pharmacy, physio, health funds",
        "subject": ["appointment", "appointment reminder", "your appointment", "prescription", "script", "repeat prescription", "test results", "referral", "medicare", "vaccination", "vaccine", "immunisation", "dental", "dentist", "check-up", "checkup", "clinic", "doctor",
                    "physio", "physiotherapy", "specialist", "pathology", "radiology", "x-ray", "ultrasound", "hospital", "pharmacy",
                    "optometrist", "eye test", "medical", "blood test", "flu shot", "covid", "health fund"],
        "sender": ["medical", "medical centre", "medical center", "clinic", "dental", "dentist", "hospital", "pharmacy", "chemist", "chemist warehouse",
                   "priceline", "medicare", "health", "physio", "physiotherapy", "gp", "doctor", "dr", "radiology", "pathology", "sonic", "healius",
                   "optometrist", "specsavers", "optical", "bupa", "medibank", "hcf", "nib", "hospital", "psychology", "counselling", "chiropractic",
                   "surgery", "healthdirect", "hotdoc", "healthengine"],
        "strong": ["doctor", "dentist", "dental", "medical", "clinic", "hospital", "prescription", "pharmacy", "chemist", "physio*", "specialist", "vaccin*",
                   "blood test", "optometrist", "optician", "gp", "test results", "x-ray", "ultrasound", "check-up", "checkup", "immunis*", "psycholog*",
                   "counsell*", "chiropract*", "medicare", "health fund", "medication", "referral", "surgery", "emergency department", "physiotherapy",
                   "bring your medicare card", "patient", "diagnosis", "symptoms", "your results", "fasting"],
        "weak": ["health", "appointment", "wellbeing", "fever", "scan", "treatment", "care", "nurse"],
    },
    # Car. Vehicles and driving: registration, servicing, fines, tolls, insurance. Senders: car brands, mechanics, NRMA/RACV, Service NSW,
    # tyre shops, toll operators. Subjects: Rego, Service reminder, Roadworthy, Infringement, Toll.
    "car": {
        "about": "vehicle emails: registration, car service and repairs, fines and tolls, tyres, licence, car insurance, roadside help",
        "subject": ["rego", "registration renewal", "car service", "service reminder", "service booking", "roadworthy", "infringement",
                    "speeding", "toll", "tolls", "tyre", "tyres", "vehicle registration", "car registration", "parking fine", "traffic fine", "driver's licence", "vehicle", "car", "mechanic", "recall",
                    "car insurance", "roadside", "windscreen", "logbook service"],
        "sender": ["toyota", "ford", "holden", "mazda", "hyundai", "kia", "honda", "nissan", "subaru", "mitsubishi", "volkswagen", "bmw", "mercedes", "tesla",
                   "mechanic", "auto", "automotive", "motors", "service centre", "nrma", "racv", "rac", "raa", "racq", "transport", "service nsw",
                   "vicroads", "bridgestone", "beaurepaires", "supercheap", "repco", "linkt", "e-toll", "toll", "tyre", "tyres", "carsales"],
        "strong": ["car", "vehicle", "rego", "mechanic", "roadworthy", "tyre", "driver's licence", "driver's license", "fuel", "parking fine",
                   "car service", "logbook service", "car insurance", "traffic fine", "speeding fine", "toll", "e-tag", "number plate", "windscreen",
                   "car wash", "ute", "motorbike", "roadside assistance", "wheel alignment", "oil change", "infringement notice", "demerit", "registration renewal", "registration due", "rego renewal"],
        "weak": ["drive", "driving", "garage", "towing", "servicing", "insurance", "licence", "kilometres", "road", "parking"],
    },
    # Paw. Pets: vets, groomers, kennels, pet food, council dog registration. Senders: "Vet", "Animal Hospital", Petbarn, RSPCA.
    "pet": {
        "about": "pet emails: vet visits, vaccinations, grooming, kennels, pet food, dog and cat registration",
        "subject": ["vet", "veterinary", "vaccination", "vaccinations", "dog", "cat", "puppy", "kitten", "pet", "pets", "grooming", "groomer", "kennel",
                    "cattery", "dog registration", "pet insurance", "microchip", "flea", "worming", "heartworm", "pet food", "dog walker"],
        "sender": ["vet", "vets", "veterinary", "animal hospital", "animal", "petbarn", "pet circle", "petstock", "pet", "pets", "rspca", "groomers",
                   "grooming", "kennels", "cattery", "dog", "dogs", "cat"],
        "strong": ["dog", "cat", "puppy", "kitten", "vet", "veterinar*", "groomer", "grooming", "pet", "dog walker", "kennel", "cattery", "flea", "worming",
                   "microchip*", "pet food", "heartworm", "dog park", "pet insurance", "vaccination due", "annual vaccination", "obedience", "leash"],
        "weak": ["walk", "vaccination", "animal", "paws", "treats", "fur"],
    },
}

# ----------------------------------------------------------------------------------------------------------------- tone
_FORMAL = [r"dear (?:sir|madam|sir/madam|mr|mrs|ms|miss|dr|prof\w*)\b", r"yours (?:sincerely|faithfully)", r"please find (?:attached|enclosed)",
           r"i am writing (?:to|regarding|in regard)", r"we are writing (?:to|regarding)", r"with reference to", r"further to (?:our|your|my)", r"pursuant to",
           r"at your earliest convenience", r"do not hesitate to (?:contact|call)", r"should you (?:have|require|need) any",
           r"we (?:would|wish) like to (?:inform|advise|confirm|remind)", r"please be advised", r"thank you for your (?:email|enquiry|inquiry|correspondence|prompt)",
           r"i trust (?:this|you)", r"\b(?:pty|ltd|llc|plc)\b\.?", r"\bconfidential\b", r"\b(?:director|ceo|cfo|coo|general manager|account manager|accounts manager|executive)\b",
           r"\bon behalf of\b", r"\battention:", r"\bplease (?:confirm|advise|revert)\b"]                  # specific: 1 point each
_FORMAL_WEAK = [r"kind regards", r"best regards", r"warm regards", r"\bkindly\b", r"\benclosed\b", r"\bsincerely\b", r"\bregards\b", r"\bas per\b",
                r"\bcorrespondence\b", r"\bregarding\b", r"\bdisclaimer\b"]                                # common in friendly mail too: half a point
_CASUAL = [r"\bhey\b", r"\bhiya\b", r"\blol\b", r"\bhaha+\b", r"\bxx+\b", r"\bmate\b", r"\bcheers\b", r"!!+", r"\bthx\b", r"\bgonna\b", r"\bwanna\b",
           r"\bbtw\b", r"[\U0001F300-\U0001FAFF]", r"\bpls\b", r"\bheaps\b", r"\bawesome\b", r"\byep\b"]
def formality(text: str) -> float:
    """How formal is this?  Specific business phrases count 1, common ones 0.5, casual ones minus 2.  3+ is clearly business-like."""
    t = text or ""
    hit = lambda p: bool(re.search(p, t, re.I))
    return sum(map(hit, _FORMAL)) + 0.5 * sum(map(hit, _FORMAL_WEAK)) - 2 * sum(map(hit, _CASUAL))


# ----------------------------------------------------------------------------------------------------------- matching
def _compile(term: str) -> str:
    """A readable phrase -> a regex: words separated by space or hyphen, trailing * = any ending, optional plural."""
    if term.startswith("re:"):
        return term[3:]
    words = []
    for w in term.lower().split():
        star = w.endswith("*")
        w = re.escape(w.rstrip("*")).replace("\\-", "[-\\s]?").replace("'", "'?")
        words.append(w + (r"\w*" if star else (r"(?:s|es)?" if len(w) > 3 and not w.endswith("s") else "")))
    return r"[\s\-]+".join(words)


def _hits(terms: list[str], text: str) -> list[str]:
    """The terms found in ``text``.  A term that is just part of a longer one that also matched ("order" inside "your order")
    is dropped, so one clue isn't counted twice."""
    seen, out = set(), []
    for t in terms:
        if t not in seen and re.search(rf"(?<![\w'])(?:{_compile(t)})(?![\w])", text, re.I):
            seen.add(t)
            out.append(t)
    plain = lambda t: t.rstrip("*").lower()
    return [t for t in out if not any(u != t and not u.startswith("re:") and plain(t) in plain(u) for u in out)]


def _spec(item: dict) -> dict:
    """The profile for a catalogue entry: built-in symbols use SPECS (plus any aliases you added); your own symbols use their name and tags."""
    if item["id"] in SPECS:
        sp = SPECS[item["id"]]
        extra = list(item.get("extra", []))
        return {**sp, "subject": [*sp["subject"], *extra], "strong": [*sp["strong"], *extra]}
    tags = [t for t in item["tags"] if t and len(t) > 2]
    return {"about": f"anything about {item['name'].lower()}", "subject": tags, "sender": tags, "strong": tags, "weak": []}


def analyse(subject: str, sender: str, body: str, raw: str, cfg: dict) -> dict[str, dict]:
    """Evidence for each symbol that could be chosen automatically: {id: {score, terms, anchor, why}} (only those with evidence).

    subject = the email's subject line(s) - sender = who it is from - body = the cleaned message - raw = the original text
    (with its sign-off, used only to judge how formal it sounds)."""
    out: dict[str, dict] = {}
    tone = formality(raw or body)
    for item in icon_lib.catalogue(cfg):
        iid = item["id"]
        if not item["enabled"] or iid in NEVER_AUTO:
            continue
        sp = _spec(item)
        score, terms, anchor, why = 0, 0, False, []
        subj_hits = _hits(sp["subject"], subject)                # the curated subject cues: one is enough on its own
        for t in subj_hits:
            score, terms, anchor = score + SUBJECT_STRONG, terms + 1, True
            why.append(f"subject '{t.rstrip('*')}'")
        inside = lambda t: any(t != u and t.rstrip("*").lower() in u.lower() for u in subj_hits)
        for t in [x for x in _hits(sp["strong"], subject) if x not in subj_hits and not inside(x)]:      # an ordinary strong word in the subject: a normal clue
            score, terms = score + BODY_STRONG, terms + 1
            why.append(f"subject '{t.rstrip('*')}'")
        for t in [x for x in _hits(sp["weak"], subject) if x not in subj_hits]:
            score, terms = score + SUBJECT_WEAK, terms + 1
        for t in _hits(sp.get("sender", []), sender):
            score, terms, anchor = score + SENDER, terms + 1, True
            why.append(f"sender '{t}'")
        for t in _hits(sp["strong"], body):
            score, terms = score + BODY_STRONG, terms + 1
            why.append(t.rstrip("*"))
        for t in _hits(sp["weak"], body):
            score, terms = score + BODY_WEAK, terms + 1
        if sp.get("tone") == "formal" and tone >= 2:
            score, terms = score + (TONE_STRONG if tone >= 3 else TONE_MILD), terms + 1
            anchor = anchor or tone >= 3
            why.append("formal tone" if tone >= 3 else "fairly formal tone")
        if score:
            out[iid] = {"score": score, "terms": terms, "anchor": anchor, "why": why}
    return out


def candidates(ev: dict[str, dict], n: int = SHORTLIST) -> list[str]:
    """The symbols worth putting in front of the AI: those with evidence, best first."""
    return sorted(ev, key=lambda i: -ev[i]["score"])[:n]


def describe(cfg: dict, ids: list[str]) -> list[str]:
    """For the AI's instructions: each shortlisted symbol with the kind of email it suits and its tell-tale subject words."""
    cat = {c["id"]: c for c in icon_lib.catalogue(cfg)}
    out = []
    for i in ids:
        c = cat.get(i)
        if not c:
            continue
        sp = _spec(c)
        cues = [t.rstrip("*") for t in sp["subject"] if not t.startswith("re:")][:5]
        out.append(f"{c['name'].lower()} = {sp['about']} (subjects like: {', '.join(cues)})")
    return out


def names_for_prompt(cfg: dict) -> list[str]:
    """Every symbol name the AI could propose (only ones that can be chosen automatically)."""
    return [c["name"].lower() for c in icon_lib.catalogue(cfg) if c["enabled"] and c["id"] not in NEVER_AUTO]


def resolve(names: list[str], cfg: dict) -> list[str]:
    """What the AI wrote ("School", "3d printer", "calendar") -> symbol ids; anything unknown is ignored."""
    table = {}
    for c in icon_lib.catalogue(cfg):
        if c["enabled"] and c["id"] not in NEVER_AUTO:
            table[icon_lib.norm(c["name"])] = c["id"]
            table[icon_lib.norm(c["id"])] = c["id"]
    out = []
    for n in names:
        iid = table.get(icon_lib.norm(n))
        if iid and iid not in out:
            out.append(iid)
    return out


def decide(ev: dict[str, dict], ai: list[str] | None = None) -> list[str]:
    """``ai`` = symbol ids the AI proposed ([] = it said "none", None = it gave no answer).  Returns 0-3 symbol ids."""
    if ai is not None and not ai:
        return []                                                # the AI wasn't sure -> neither are we
    if ai is not None:                                           # the AI chose from the shortlist and the email backs each one up
        ok = {i: ev[i]["score"] for i in ai if i in ev and ev[i]["score"] >= AI_SCORE}
        return sorted(ok, key=lambda i: -ok[i])[:MAX_SYMBOLS]
    ok = {i: e["score"] for i, e in ev.items() if e["score"] >= SOLO_SCORE and (e["anchor"] or e["terms"] >= SOLO_TERMS)}
    if not ok:
        return []
    ranked = sorted(ok, key=lambda i: -ok[i])
    best = ok[ranked[0]]
    # a weaker symbol stays if it is nearly as well supported, or has its own clear subject clue (a "Receipt" subject keeps Money
    # even on a shop's order email)
    return [i for i in ranked if ok[i] >= RELATIVE * best or (ev[i]["anchor"] and ok[i] >= SOLO_SCORE)][:MAX_SYMBOLS]


def choose(text: str, subject: str, cfg: dict, ai: list[str] | None = None, sender: str = "", raw: str = "") -> list[str]:
    return decide(analyse(subject, sender, text, raw, cfg), ai)


def why(ev: dict[str, dict], ids: list[str], cfg: dict) -> str:
    """A short plain-English reason for the history notes: 'Money (subject 'receipt'), Work (formal tone)'."""
    names = {c["id"]: c["name"] for c in icon_lib.catalogue(cfg)}
    return ", ".join(f"{names.get(i, i)} ({', '.join(ev[i]['why'][:2])})" if i in ev and ev[i]["why"] else names.get(i, i) for i in ids)


def pick(subject: str, sender: str, body: str, raw: str, cfg: dict, info: dict | None) -> tuple[list[str], list[str] | None, dict]:
    """Everything in one go for a finished summary.  Returns (chosen ids, the AI's own proposals as ids or None, the evidence)."""
    info = info or {}
    ev = analyse(subject, sender, body, raw, cfg)
    ai = resolve(info["symbols_raw"], cfg) if info.get("used_ai") and info.get("symbols_raw") is not None else None
    return decide(ev, ai), ai, ev
