/**
 * Jack in a Box - Google Sheet link.
 *
 * Paste this whole file into the Apps Script editor of the spreadsheet that your Google Form fills in
 * (Extensions > Apps Script), then Deploy > New deployment > Web app:
 *     Execute as: Me          Who has access: Anyone
 * Copy the "Web app URL" into Jack in a Box.  Nothing is public: every request must carry the secret code
 * below, and this script only ever does three things:  list the rows, hand over an uploaded image, and
 * delete a row once Jack in a Box has printed it.
 */
const TOKEN = '__TOKEN__';          // the secret code Jack in a Box sends with every request
const SHEET_NAME = '';              // blank = the first tab ("Form responses 1")
const MAX_FILE_BYTES = 8 * 1024 * 1024;

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (!p.token || p.token !== TOKEN) return out_({ ok: false, error: 'wrong secret code (token)' });
  try {
    const sheet = sheet_();
    switch (p.action) {
      case 'rows': return out_(rows_(sheet));
      case 'file': return out_(file_(p.id));
      case 'delete': return out_(delete_(sheet, String(p.ids || '').split(',').filter(String)));
      default: return out_({ ok: true, message: 'Jack in a Box sheet link is working', rows: Math.max(0, sheet.getLastRow() - 1) });
    }
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  }
}

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const s = SHEET_NAME ? ss.getSheetByName(SHEET_NAME) : ss.getSheets()[0];
  if (!s) throw new Error('No tab named ' + SHEET_NAME);
  return s;
}

// A cell as plain text.  Dates are written in the SPREADSHEET'S own timezone ("2026-11-05T17:30:00") so Jack in a Box
// can read the "Schedule Send" answers; a time-only answer (Google stores it as a date in 1899) is sent as it is displayed.
function cell_(v, shown, tz) {
  if (v instanceof Date) {
    if (v.getFullYear() < 1900) return String(shown || '');
    return Utilities.formatDate(v, tz, "yyyy-MM-dd'T'HH:mm:ss");
  }
  return String(v === null || v === undefined ? '' : v);
}

// The older text of a cell, used ONLY to fingerprint rows - it must never change, or rows already waiting to be
// deleted would look like new rows.
function raw_(v) {
  return v instanceof Date ? v.toISOString() : String(v === null || v === undefined ? '' : v);
}

// A short fingerprint of a row, so the same row can be found again later even after other rows were deleted.
function id_(row) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, row.map(raw_).join('␟'));
  return Utilities.base64EncodeWebSafe(bytes).slice(0, 16);
}

function rows_(sheet) {
  const range = sheet.getDataRange();
  const data = range.getValues();
  const shown = range.getDisplayValues();
  const tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  if (data.length < 1) return { ok: true, headers: [], rows: [], timezone: tz };
  const headers = data[0].map(function (c, j) { return cell_(c, shown[0][j], tz); });
  const rows = [];
  for (let i = 1; i < data.length && rows.length < 200; i++) {
    const r = data[i];
    if (r.every(function (c) { return raw_(c).trim() === ''; })) continue;      // skip blank rows
    const values = {};
    headers.forEach(function (h, j) { values[h] = cell_(r[j], shown[i][j], tz); });
    rows.push({ id: id_(r), values: values });
  }
  return { ok: true, headers: headers, rows: rows, timezone: tz };
}

function file_(id) {
  if (!/^[A-Za-z0-9_-]{10,}$/.test(String(id || ''))) throw new Error('bad file id');
  const f = DriveApp.getFileById(id);
  const blob = f.getBlob();
  const bytes = blob.getBytes();
  if (bytes.length > MAX_FILE_BYTES) return { ok: false, error: 'file too big (' + bytes.length + ' bytes)' };
  return { ok: true, name: f.getName(), mime: blob.getContentType(), data: Utilities.base64Encode(bytes) };
}

function delete_(sheet, ids) {
  const data = sheet.getDataRange().getValues();
  let deleted = 0;
  for (let i = data.length - 1; i >= 1; i--) {           // bottom-up so the row numbers stay valid
    if (ids.indexOf(id_(data[i])) !== -1) { sheet.deleteRow(i + 1); deleted++; }
  }
  return { ok: true, deleted: deleted };
}
