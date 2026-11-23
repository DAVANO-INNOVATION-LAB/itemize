/* Optional, same-origin OCR. Receipt bytes are passed only to the local
 * worker; no analytics, remote OCR service, or CDN is involved. */
'use strict';

window.Receipt = (() => {
  const asset = (name) => new URL('vendor/ocr/' + name, document.baseURI).href;
  let loading;

  function engine() {
    if (window.Tesseract) return Promise.resolve(window.Tesseract);
    if (!loading) loading = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = asset('tesseract.min.js');
      script.onload = () => window.Tesseract ? resolve(window.Tesseract) : reject(unavailable());
      script.onerror = () => { script.remove(); reject(unavailable()); };
      document.head.append(script);
    }).catch((error) => { loading = null; throw error; });
    return loading;
  }

  function unavailable() {
    return new Error('Photo reading isn’t available in this copy of Itemize. Choose a text PDF from your provider or paste the bill text.');
  }

  async function readPhoto(file, signal) {
    const lib = await engine();
    if (signal.aborted) return '';
    let worker, timer;
    const terminate = () => { if (worker) worker.terminate().catch(() => {}); };
    let stop;
    const stopped = new Promise((_, reject) => {
      stop = () => reject(new Error('Photo reading stopped.'));
      timer = setTimeout(stop, 75000);
    });
    signal.addEventListener('abort', stop, { once: true });
    const input = async () => {
      // Rasterize through the browser’s decoder when available, respecting
      // EXIF orientation and limiting work on phone-size images.
      if (typeof createImageBitmap !== 'function') return file;
      let bitmap;
      try { bitmap = await createImageBitmap(file); }
      catch { return file; }
      try {
        const scale = Math.min(1, 2600 / Math.max(bitmap.width, bitmap.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        const ctx = canvas.getContext('2d');
        if (!ctx) return file;
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        return await new Promise((resolve) => canvas.toBlob((blob) => resolve(blob || file), 'image/png'));
      } finally {
        bitmap.close();
      }
    };
    const read = async () => {
      const photo = await input();
      if (signal.aborted) return '';
      worker = await lib.createWorker('eng', 1, {
        workerPath: asset('worker.min.js'),
        corePath: asset('tesseract-core-lstm.wasm.js'),
        langPath: asset(''),
        workerBlobURL: false,
        cacheMethod: 'none',
        errorHandler: stop,
      });
      if (signal.aborted) { terminate(); return ''; }
      await worker.setParameters({ tessedit_pageseg_mode: '6', preserve_interword_spaces: '1' });
      const { data } = await worker.recognize(photo);
      if (!data.text.trim()) throw new Error('No text was readable.');
      return data.text;
    };
    try {
      return await Promise.race([read(), stopped]);
    } catch {
      if (signal.aborted) return '';
      if (!worker) throw unavailable();
      throw new Error('We couldn’t read this photo. Try a well-lit photo showing the full page, or choose a text PDF.');
    } finally {
      clearTimeout(timer);
      signal.removeEventListener('abort', stop);
      terminate();
    }
  }

  return { readPhoto };
})();
