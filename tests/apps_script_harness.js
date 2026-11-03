// Runs the real app/apps_script.js against stand-ins for Google's services and prints what it does.
const fs = require('fs'), vm = require('vm'), crypto = require('crypto');
const src = fs.readFileSync(process.argv[2], 'utf8').replace('__TOKEN__', 'secret');

class Sheet {
  constructor(data) { this.data = data; }
  getDataRange() {
    // time-only answers are dates in 1899; getDisplayValues shows them the way the sheet displays them
    const shown = r => r.map(c => c instanceof Date && c.getFullYear() < 1900 ? '5:30:00 PM' : String(c));
    return { getValues: () => this.data.map(r => r.slice()), getDisplayValues: () => this.data.map(shown) };
  }
  getLastRow() { return this.data.length; }
  deleteRow(n) { this.data.splice(n - 1, 1); }
}
const sheet = new Sheet([
  ['Timestamp', 'Email Address', 'Body', 'Image', 'Schedule Send (DATE)', 'Schedule Send (TIME)'],
  [new Date('2026-11-03T01:00:00Z'), 'me@x.com', 'first', '', '', ''],
  ['', '', '', '', '', ''],                                                    // blank row: must be skipped
  [new Date('2026-11-03T02:00:00Z'), 'me@x.com', 'second', 'https://drive.google.com/open?id=GOODFILEID0001', new Date('2026-11-04T13:00:00Z'), new Date(Date.UTC(1899, 11, 30, 7, 30))],
  [new Date('2026-11-03T03:00:00Z'), 'me@x.com', 'third', '', '', ''],
  [new Date('2026-11-03T04:00:00Z'), 'me@x.com', 'fourth', '', '', ''],
]);
global.SpreadsheetApp = { getActiveSpreadsheet: () => ({ getSheets: () => [sheet], getSheetByName: () => null, getSpreadsheetTimeZone: () => 'Australia/Sydney' }) };
global.ContentService = { MimeType: { JSON: 'json' }, createTextOutput: s => ({ text: s, setMimeType() { return this; } }) };
global.Utilities = {
  DigestAlgorithm: { MD5: 'MD5' },
  computeDigest: (a, s) => Array.from(crypto.createHash('md5').update(s).digest()),
  base64EncodeWebSafe: b => Buffer.from(b).toString('base64url'),
  base64Encode: b => Buffer.from(b).toString('base64'),
  formatDate: (d, tz, fmt) => d.toLocaleString('sv-SE', { timeZone: tz }).replace(' ', 'T'),     // "yyyy-MM-dd'T'HH:mm:ss"
};
global.DriveApp = {
  getFileById: id => {
    if (id === 'GOODFILEID0001') return { getName: () => 'pic.png', getBlob: () => ({ getBytes: () => [137, 80, 78, 71], getContentType: () => 'image/png' }) };
    if (id === 'HUGEFILEID00001') return { getName: () => 'big.png', getBlob: () => ({ getBytes: () => ({ length: 9 * 1024 * 1024 }), getContentType: () => 'image/png' }) };
    throw new Error('File not found: ' + id);
  },
};
vm.runInThisContext(src);
const call = p => JSON.parse(doGet({ parameter: p }).text);
const out = {};
out.noToken = call({ action: 'rows' });
out.wrongToken = call({ token: 'nope', action: 'rows' });
out.ping = call({ token: 'secret' });
out.rows = call({ token: 'secret', action: 'rows' });
out.file = call({ token: 'secret', action: 'file', id: 'GOODFILEID0001' });
out.badFileId = call({ token: 'secret', action: 'file', id: '../etc/passwd' });
out.missingFile = call({ token: 'secret', action: 'file', id: 'NOSUCHFILE0001' });
out.hugeFile = call({ token: 'secret', action: 'file', id: 'HUGEFILEID00001' });
const ids = out.rows.rows.map(r => r.id);
out.deleteMiddleAndLast = call({ token: 'secret', action: 'delete', ids: [ids[1], ids[3]].join(',') });
out.afterDelete = call({ token: 'secret', action: 'rows' }).rows.map(r => r.values.Body);
out.deleteNothing = call({ token: 'secret', action: 'delete', ids: 'doesnotexist' });
console.log(JSON.stringify(out));
