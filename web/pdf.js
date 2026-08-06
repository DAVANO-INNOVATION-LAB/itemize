/* Minimal PDF text-layer extractor — no dependencies.
 *
 * Most bills a hospital portal hands you are PDFs with a real text layer, so
 * they can be read exactly rather than guessed at by OCR. This pulls that layer
 * out using the browser's native DecompressionStream for FlateDecode.
 *
 * Deliberate limits: this reads text-layer PDFs only. Scanned/photographed
 * PDFs have no text layer and will return nothing — the caller must say so
 * plainly rather than presenting an empty result as "no charges found".
 * Encrypted PDFs and CID/Type0 fonts with custom encodings are not handled.
 */
'use strict';

const PDF = (() => {
  const latin1 = (bytes) => {
    let s = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return s;
  };

  async function inflate(bytes, format) {
    const ds = new DecompressionStream(format);
    const stream = new Blob([bytes]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  /* Locate every `stream … endstream` span and note whether it is Flate'd. */
  function findStreams(bytes) {
    const s = latin1(bytes);
    const out = [];
    let i = 0;
    while ((i = s.indexOf('stream', i)) !== -1) {
      // Ignore the tail of the word "endstream".
      if (s.slice(i - 3, i) === 'end') { i += 6; continue; }
      const dictStart = s.lastIndexOf('<<', i);
      const dict = dictStart === -1 ? '' : s.slice(dictStart, i);
      let p = i + 6;
      if (s[p] === '\r') p++;
      if (s[p] === '\n') p++;
      const kw = s.indexOf('endstream', p);
      if (kw === -1) break;

      // The bytes between the data and the `endstream` keyword are an EOL that
      // is NOT part of the stream. DecompressionStream rejects trailing junk
      // after a zlib stream, so the end offset has to be exact: prefer the
      // dictionary's /Length, and otherwise trim the EOL back off.
      let end = kw;
      const lenM = /\/Length\s+(\d+)(?!\s+\d+\s+R)/.exec(dict);
      if (lenM) {
        const n = parseInt(lenM[1], 10);
        if (p + n <= kw) end = p + n;
      }
      while (end > p && (s[end - 1] === '\n' || s[end - 1] === '\r')) end--;

      out.push({
        data: bytes.subarray(p, end),
        flate: /\/FlateDecode/.test(dict),
        image: /\/Subtype\s*\/Image/.test(dict),
      });
      i = kw + 9;
    }
    return out;
  }

  /* Decode a PDF literal string, handling escapes and octal codes. */
  function decodeLiteral(raw) {
    let out = '';
    for (let i = 0; i < raw.length; i++) {
      const c = raw[i];
      if (c !== '\\') { out += c; continue; }
      const n = raw[++i];
      if (n === undefined) break;
      if (n === 'n') out += '\n';
      else if (n === 'r') out += '\r';
      else if (n === 't') out += '\t';
      else if (n === 'b' || n === 'f') out += ' ';
      else if (n >= '0' && n <= '7') {
        let oct = n;
        while (oct.length < 3 && raw[i + 1] >= '0' && raw[i + 1] <= '7') oct += raw[++i];
        out += String.fromCharCode(parseInt(oct, 8));
      } else if (n === '\n') { /* line continuation */ }
      else out += n;
    }
    return out;
  }

  const decodeHex = (raw) => {
    const h = raw.replace(/[^0-9A-Fa-f]/g, '');
    let out = '';
    for (let i = 0; i + 1 < h.length; i += 2) out += String.fromCharCode(parseInt(h.substr(i, 2), 16));
    return out;
  };

  /* Walk a content stream, keeping enough text-position state to rebuild rows. */
  function parseContent(text) {
    const TOKEN = /\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>|\[|\]|[-+]?[\d.]+|[A-Za-z'"*]+/g;
    const rows = [];
    let nums = [], cur = '', y = null, pendingArray = false;

    const flush = () => {
      // Normalise whitespace but preserve the two-space column gaps inserted on
      // horizontal moves: collapsing everything to single spaces would glue a
      // description ending in "LEVEL 3" to a units column of "1".
      const t = cur.replace(/[\r\n\t]+/g, ' ').replace(/ {3,}/g, '  ').trim();
      if (t) rows.push({ y, text: t });
      cur = '';
    };

    let m;
    while ((m = TOKEN.exec(text)) !== null) {
      const tok = m[0];

      if (tok[0] === '(') { cur += decodeLiteral(tok.slice(1, -1)); continue; }
      if (tok[0] === '<' && tok[1] !== '<') { cur += decodeHex(tok.slice(1, -1)); continue; }
      if (tok === '[') { pendingArray = true; nums = []; continue; }
      if (tok === ']') { pendingArray = false; continue; }
      if (/^[-+]?[\d.]+$/.test(tok)) {
        const v = parseFloat(tok);
        // Inside a TJ array, large negative kerning means a word gap.
        if (pendingArray && v <= -100) cur += ' ';
        else nums.push(v);
        continue;
      }

      switch (tok) {
        case 'Td': case 'TD': {
          const ny = nums.length >= 2 ? nums[nums.length - 1] : null;
          // A vertical move starts a new visual row; a purely horizontal one
          // is the next column of the same row and needs a separator, or
          // "2026-03-14" and "99283" arrive glued together as one token.
          if (ny !== null && (y === null || Math.abs(ny) > 0.5)) flush();
          else if (cur && !/\s$/.test(cur)) cur += '  ';
          y = ny;
          break;
        }
        case 'Tm': {
          const ny = nums.length >= 6 ? nums[nums.length - 1] : null;
          if (y !== null && ny !== null && Math.abs(ny - y) > 1.5) flush();
          else if (cur && !/\s$/.test(cur)) cur += '  ';
          y = ny;
          break;
        }
        case 'T*': case "'": case '"': flush(); break;
        case 'ET': case 'BT': flush(); break;
        default: break;
      }
      nums = [];
    }
    flush();
    return rows;
  }

  /* Group fragments that share a baseline into single lines. */
  function assemble(rows) {
    const lines = [];
    let curY = null, buf = [];
    for (const r of rows) {
      if (curY === null || r.y === null || Math.abs(r.y - curY) > 1.5) {
        if (buf.length) lines.push(buf.join('  '));
        buf = [r.text];
        curY = r.y;
      } else {
        buf.push(r.text);
      }
    }
    if (buf.length) lines.push(buf.join('  '));
    return lines.filter((l) => l.trim());
  }

  async function extractText(arrayBuffer) {
    const bytes = new Uint8Array(arrayBuffer);
    if (latin1(bytes.subarray(0, 5)) !== '%PDF-') throw new Error('not a PDF file');
    if (/\/Encrypt\b/.test(latin1(bytes.subarray(0, Math.min(bytes.length, 4096))))) {
      throw new Error('this PDF is encrypted; save an unprotected copy and retry');
    }

    const out = [];
    for (const s of findStreams(bytes)) {
      if (s.image) continue;
      let data = s.data;
      if (s.flate) {
        let ok = false;
        for (const fmt of ['deflate', 'deflate-raw']) {
          try { data = await inflate(s.data, fmt); ok = true; break; } catch (e) { /* try next */ }
        }
        if (!ok) continue;
      }
      const text = latin1(data);
      if (!/\b(Tj|TJ)\b/.test(text)) continue;      // not a content stream
      out.push(...assemble(parseContent(text)));
    }
    return out.join('\n');
  }

  return { extractText };
})();
