# Tool Logos

Drop logo files in this folder for the Export page to display them next to
their tool names.

## Naming convention

Use **lowercase** filenames matching the `ToolKey` identifier in
`apps/web/components/ToolBadge.tsx`. SVG is preferred for crisp scaling; PNG is
kept for existing wordmark-style assets.

## Supported file names

```
vosviewer.png
bibliometrix.png
biblioshiny.svg
citespace.svg
zotero.svg
mendeley.svg
endnote.svg
jabref.svg
overleaf.svg
gephi.svg
... etc.
```

The badge component uses explicit `logoSrc` paths from
`apps/web/components/ToolBadge.tsx`, so add the file and wire the path there.

## Current asset set

- Existing raster wordmarks: `vosviewer.png`, `bibliometrix.png`.
- Downloaded SVG assets: `zotero.svg`, `mendeley.svg`, `overleaf.svg`,
  `latex.svg`, `python.svg`, `r.svg`, `excel.svg`, `tableau.svg`,
  `powerbi.svg`, `openrefine.svg`, `gephi.svg`, `jabref.svg`.
- Local SVG tool marks for tools without a reliable clean public SVG in this
  pass: `biblioshiny.svg`, `citespace.svg`, `bibexcel.svg`, `histcite.svg`,
  `citnetexplorer.svg`, `endnote.svg`, `citavi.svg`, `refworks.svg`,
  `papers.svg`, `scite.svg`.

## Copyright

These are third-party trademarks. Use them only to identify compatible tools;
each tool's licensing and trademark rules still apply. For tools without a
verified distributable logo, the SVG here is a neutral local mark rather than
an official logo.

Official sources:
- **VOSviewer**: https://www.vosviewer.com/
- **bibliometrix**: https://www.bibliometrix.org/
- **CiteSpace**: https://citespace.podia.com/
- **Zotero**: https://www.zotero.org/support/brand
- **Mendeley**: https://www.mendeley.com/
- **EndNote**: https://endnote.com/
- **JabRef**: https://www.jabref.org/
- **Overleaf**: https://www.overleaf.com/for/about/press
- **OpenRefine**: https://openrefine.org/
- **Gephi**: https://gephi.org/
- **Simple Icons**: https://simpleicons.org/ and
  https://api.iconify.design/ for the CC0 single-color SVG icons used by
  Zotero, Mendeley, Overleaf, LaTeX, Python, R, Excel, Tableau and Power BI.

If a logo file is missing, the badge falls back to a tasteful text-only
chip — so it's safe to leave this folder empty.
