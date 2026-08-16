import * as pdfjsLib from "/static/vendor/pdfjs-4.1.392/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs-4.1.392/pdf.worker.min.mjs";

const root = document.querySelector(".resume-pdfjs-page");
const canvas = document.querySelector("#pdfjs-canvas");
const status = document.querySelector("#pdfjs-status");
const pdfUrl = root.dataset.pdfUrl;

window.PDFViewerApplication = { url: new URL(pdfUrl, window.location.href).href };

try {
  const documentTask = pdfjsLib.getDocument({ url: pdfUrl, isEvalSupported: true });
  const pdf = await documentTask.promise;
  const page = await pdf.getPage(1);
  const viewport = page.getViewport({ scale: 1.5 });
  const context = canvas.getContext("2d");
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({ canvasContext: context, viewport }).promise;
  status.textContent = `${pdf.numPages}페이지 · PDF.js 4.1.392`;
} catch (error) {
  status.textContent = "PDF 미리보기를 불러오지 못했습니다.";
}
