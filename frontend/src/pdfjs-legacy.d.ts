/** The legacy pdf.js build ships no adjacent type declarations (unlike the package
 * root, which maps to types/src/pdf.d.ts) -- but it exposes the exact same API
 * surface. Re-export the root types so `tsc -b` (used by `npm run build`) accepts
 * the legacy import. See PdfOverlay.tsx for why the legacy build is used at all
 * (Map upsert compat). */
declare module "pdfjs-dist/legacy/build/pdf.min.mjs" {
  export * from "pdfjs-dist";
}
