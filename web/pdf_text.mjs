/* Read PDF text, not actions, links, forms or embedded scripts. The package,
 * worker, predefined character maps and fonts are staged on this origin. */
const assets = new URL('vendor/ocr/pdfjs/', import.meta.url);
const MAX_PAGES = 100;
const MAX_ITEMS = 100000;
const MAX_TEXT = 1000000;
const MAX_BYTES = 25 * 1024 * 1024;
const TIME_LIMIT = 30000;
let engine;

function failure(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

// Common receipt export: amounts are vertically centered beside a wrapped
// two-line description. Narrow safety rule: a clearly labelled receipt table,
// one isolated numeric center row, and two exclusive description fragments at
// nearly equal distances. Never attach an unbalanced or cross-column fragment.
function rejoinCenteredDescriptions(rows) {
  const rendered = [];
  let header;
  const price = /^\(?-?\$[ \t]?(?:[0-9]{1,12}|[0-9]{1,3}(?:,[0-9]{3}){1,3})\.[0-9]{2}\)?$/;
  const qty = /^[0-9]{1,4}(?:\.[0-9]{1,3})?$/;
  function receiptHeader(boxes) {
    const words = boxes.map(b => b.str.toLowerCase().trim());
    const q = words.findIndex(s => /^(?:qty\.?|quantity)$/.test(s));
    if (words[0] !== 'description' || q < 1 || q > 2 ||
        !['price', 'unit price'].includes(words[q + 1])) return null;
    const names = words.slice(q + 2).join('|');
    if (names !== 'total' && names !== 'discount|total') return null;
    return {first:boxes[0], boundary:boxes[1].x, fields:boxes.slice(q)};
  }
  function fits(b, column, previous, next) {
    const leftLimit = previous ? (column.x + previous.x) / 2 : column.x - 14;
    const rightLimit = next ? (column.end + next.x) / 2 : column.end + 14;
    const center = (b.x + b.end) / 2;
    return center >= leftLimit && center <= rightLimit
      && Math.min(Math.abs(b.x - column.x), Math.abs(b.end - column.end)) <= 12;
  }
  function descriptionBox(boxes, h) {
    if (boxes.length !== 1) return null;
    const [b] = boxes;
    return Math.abs(b.x - h.first.x) <= 5 && b.end < h.boundary - 2 &&
      /\p{L}{2}/u.test(b.str) && !/^(?:description|subtotal|grand total|amount due|payments?)\b/i.test(b.str) ? b : null;
  }
  for (let i = 0; i < rows.length; ++i) {
    const row = rows[i];
    const seen = receiptHeader(row.boxes);
    if (seen) header = {...seen, y:row.y};
    if (header && !seen && header.y - row.y <= 450 && i + 2 < rows.length) {
      const mid = rows[i + 1], lower = rows[i + 2];
      const aboveGap = row.y - mid.y, belowGap = mid.y - lower.y;
      const top = descriptionBox(row.boxes, header), bottom = descriptionBox(lower.boxes, header);
      const fields = mid.boxes;
      if (top && bottom && Math.abs(top.x - bottom.x) <= 1.5 &&
          aboveGap >= 3 && aboveGap <= 8 && belowGap >= 3 && belowGap <= 8 &&
          Math.abs(aboveGap - belowGap) <= 1 &&
          fields.length === header.fields.length &&
          qty.test(fields[0].str) && fields.slice(1).every(b => price.test(b.str)) &&
          fields.every((b, j) => (!j || fields[j - 1].end <= b.x) &&
            fits(b, header.fields[j], header.fields[j - 1], header.fields[j + 1]))) {
        rendered.push({y:mid.y, boxes:[
          {x:top.x, end:Math.max(top.end,bottom.end), str:top.str + ' ' + bottom.str},
          ...fields,
        ]});
        i += 2;
        continue;
      }
    }
    rendered.push(row);
  }
  return rendered;
}

export function assemblePage(items) {
  const rows = [];
  const boxes = [];
  for (const item of items) {
    if (typeof item.str !== 'string' || !item.str.trim() || !item.transform || item.dir === 'ttb') continue;
    const [, , , , x, y] = item.transform;
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(item.width)) continue;
    const str = item.str.replace(/[\r\n\t]+/g, ' ').trim();
    if (str) boxes.push({x, y, end:x + Math.max(0,item.width), str});
  }
  boxes.sort((a,b) => b.y - a.y || a.x - b.x);
  for (const cell of boxes) {
    let row = rows[rows.length - 1];
    if (!row || Math.abs(row.y - cell.y) > 1.5) {
      row = {y:cell.y, boxes:[]};
      rows.push(row);
    }
    row.boxes.push(cell);
  }
  for (const row of rows) row.boxes.sort((a,b) => a.x - b.x);
  return rejoinCenteredDescriptions(rows).map(row => {
    let text = '', lastRight = null;
    for (const cell of row.boxes) {
      if (lastRight !== null) {
        const gap = cell.x - lastRight;
        // Do not insert a space within a split currency number or glyph run.
        text += gap >= 8 ? '  ' : gap >= 1.1 ? ' ' : '';
      }
      text += cell.str.replace(/\s+/g, ' ');
      lastRight = Math.max(lastRight ?? cell.end, cell.end);
    }
    return text;
  }).join('\n');
}

// Prevent a PDF-supplied resource name from being turned into an arbitrary
// fetch (even a same-origin path outside this verified static asset set).
class StagedBinaryData {
  async fetch({kind, filename}) {
    const cmap = kind === 'cMapUrl' && /^[A-Za-z0-9_-]+\.bcmap$/.test(filename);
    const font = kind === 'standardFontDataUrl' && /^[A-Za-z0-9_-]+\.(?:pfb|ttf)$/.test(filename);
    if (!cmap && !font) throw failure('PDF_RESOURCE', 'An unsupported PDF resource was requested.');
    const url = new URL((cmap ? 'cmaps/' : 'standard_fonts/') + filename, assets);
    const response = await fetch(url, {mode:'same-origin', redirect:'error', referrerPolicy:'no-referrer'});
    if (!response.ok) throw failure('PDF_ASSETS_REQUIRED', 'A prepared local font resource is missing.');
    return new Uint8Array(await response.arrayBuffer());
  }
}

export async function extractPdfText(bytes, signal) {
  if (bytes.length > MAX_BYTES) throw failure('PDF_LIMIT', 'This PDF exceeds the 25 MB text-reading limit.');
  let lib;
  try {
    if (!engine) engine = import(new URL('pdf.mjs', assets).href).catch(error => { engine = null; throw error; });
    lib = await engine;
  } catch {
    throw failure('PDF_ASSETS_REQUIRED', 'The prepared local PDF reader is unavailable.');
  }
  if (signal?.aborted) throw failure('PDF_ABORT', 'Reading stopped.');
  const workerUrl = new URL('pdf.worker.mjs', assets);
  lib.GlobalWorkerOptions.workerSrc = workerUrl.href;
  let port, worker, task;
  try {
    if (typeof window !== 'undefined') {
      if (workerUrl.origin !== location.origin) {
        throw failure('PDF_ASSETS_REQUIRED', 'The PDF reader must be served on this origin.');
      }
      port = new Worker(workerUrl, {type:'module'});
      worker = new lib.PDFWorker({port});
    }
    task = lib.getDocument({
    worker,
    data: bytes.slice(),
    cMapUrl: new URL('cmaps/', assets).href,
    cMapPacked: true,
    standardFontDataUrl: new URL('standard_fonts/', assets).href,
    disableFontFace: true,
    useSystemFonts: false,
    isEvalSupported: false,
    enableXfa: false,
    useWasm: false,
    useWorkerFetch: false,
    BinaryDataFactory: StagedBinaryData,
    disableWorkerRendering: true,
    disableRange: true,
    stopAtErrors: true,
    disableAutoFetch: true,
    });
  } catch (error) {
    worker?.destroy();
    port?.terminate();
    if (error?.code) throw error;
    throw failure('PDF_ASSETS_REQUIRED', 'The prepared local PDF worker could not start.');
  }
  let timer, abort, workerError;
  const stopped = new Promise((_, reject) => {
    abort = () => reject(failure('PDF_ABORT', 'Reading stopped.'));
    timer = setTimeout(() => reject(failure('PDF_LIMIT', 'This PDF took too long to read safely. Try fewer pages.')), TIME_LIMIT);
    workerError = () => reject(failure('PDF_ASSETS_REQUIRED', 'The prepared local PDF worker could not load.'));
    port?.addEventListener('error', workerError, {once:true});
    signal?.addEventListener('abort', abort, {once:true});
  });
  async function read() {
    const doc = await task.promise;
    if (doc.numPages > MAX_PAGES) throw failure('PDF_LIMIT', 'Choose a PDF with at most 100 pages.');
    let itemCount = 0, textCount = 0;
    const pages = [];
    for (let i = 1; i <= doc.numPages; i++) {
      const page = await doc.getPage(i);
      try {
        const stream = page.streamTextContent({includeMarkedContent:false});
        const reader = stream.getReader();
        const items = [];
        for (;;) {
          const {done, value} = await reader.read();
          if (done) break;
          for (const item of value.items) {
            itemCount++;
            textCount += typeof item.str === 'string' ? item.str.length : 0;
            if (itemCount > MAX_ITEMS || textCount > MAX_TEXT) {
              void reader.cancel().catch(() => {});
              throw failure('PDF_LIMIT', 'Too much PDF text to read safely. Try fewer pages.');
            }
            items.push(item);
          }
        }
        pages.push(assemblePage(items));
      } finally { page.cleanup(); }
    }
    return pages.join('\n\n').trim();
  }
  try {
    return await Promise.race([read(), stopped]);
  } catch (err) {
    if (err?.name === 'PasswordException') throw failure('PDF_PASSWORD', 'This PDF is protected. Use an unprotected copy.');
    if (/setting up fake worker failed|failed to fetch dynamically imported module/i.test(err?.message || '')) {
      throw failure('PDF_ASSETS_REQUIRED', 'The prepared local PDF worker is unavailable.');
    }
    throw err;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    port?.removeEventListener('error', workerError);
    let cleanupTimer;
    try {
      await Promise.race([
        task.destroy().catch(() => {}),
        new Promise(resolve => { cleanupTimer = setTimeout(resolve, 1000); }),
      ]);
    } finally {
      clearTimeout(cleanupTimer);
      worker?.destroy();
      port?.terminate();
    }
  }
}
