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
  return rows.map(row => {
    let text = '', lastRight = null;
    row.boxes.sort((a,b) => a.x - b.x);
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
