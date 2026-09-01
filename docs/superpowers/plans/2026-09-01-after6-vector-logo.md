# AFTER6 White Vector Logo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a font-independent, pure-white `After6` SVG logo with a transparent background and no embedded raster content.

**Architecture:** The SVG uses a fixed `viewBox` and two independently adjustable groups: an angular A emblem with partial circular arcs, and a hand-built `fter6` wordmark. A focused pytest parses the SVG as XML and enforces portability, accessibility, color, transparency, and vector-only constraints before a PyMuPDF render is visually inspected.

**Tech Stack:** SVG 1.1-compatible XML, Python 3.11 standard library, pytest, PyMuPDF 1.27

## Global Constraints

- Create `output/after6-logo-white.svg`.
- The combined mark must read `After6`: the emblem is `A`, followed by lowercase `fter` and numeral `6`.
- Every visible logo element must use solid white `#FFFFFF`/`#fff`.
- The background must be genuinely transparent; do not add a background rectangle.
- Do not use `<image>`, `<text>`, external fonts, raster data, gradients, filters, shadows, or metallic effects.
- Keep the logo horizontal with generous transparent margins.

---

### Task 1: Add a structural SVG contract test

**Files:**
- Create: `tests/test_after6_logo_svg.py`
- Test: `tests/test_after6_logo_svg.py`

**Interfaces:**
- Consumes: design requirements from `docs/superpowers/specs/2026-09-01-after6-vector-logo-design.md`
- Produces: an executable contract for `output/after6-logo-white.svg`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from xml.etree import ElementTree as ET


SVG_PATH = Path(__file__).parents[1] / "output" / "after6-logo-white.svg"
SVG_NS = "{http://www.w3.org/2000/svg}"


def test_after6_logo_is_portable_white_vector_with_transparent_background():
    assert SVG_PATH.exists(), f"missing vector asset: {SVG_PATH}"

    root = ET.parse(SVG_PATH).getroot()
    assert root.tag == f"{SVG_NS}svg"
    assert root.attrib["viewBox"] == "0 0 1500 520"
    assert root.attrib["aria-label"] == "After6"

    elements = list(root.iter())
    local_names = {element.tag.rsplit("}", 1)[-1] for element in elements}
    assert "image" not in local_names
    assert "text" not in local_names
    assert "rect" not in local_names
    assert "linearGradient" not in local_names
    assert "radialGradient" not in local_names
    assert "filter" not in local_names

    ids = {element.attrib.get("id") for element in elements}
    assert {"after6-emblem", "after6-wordmark"} <= ids

    visible = [
        element
        for element in elements
        if element.tag.rsplit("}", 1)[-1] in {"path", "polygon"}
    ]
    assert len(visible) >= 8
    for element in visible:
        fill = element.attrib.get("fill", "#fff").lower()
        stroke = element.attrib.get("stroke", "none").lower()
        assert fill in {"#fff", "#ffffff", "none"}
        assert stroke in {"#fff", "#ffffff", "none"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_after6_logo_svg.py`

Expected: FAIL with `missing vector asset` because `output/after6-logo-white.svg` does not exist.

- [ ] **Step 3: Commit the failing contract test**

```powershell
git add -- tests/test_after6_logo_svg.py
git commit -m "test: define After6 vector logo contract"
```

---

### Task 2: Build the font-independent SVG asset

**Files:**
- Create: `output/after6-logo-white.svg`
- Test: `tests/test_after6_logo_svg.py`

**Interfaces:**
- Consumes: the XML and visual constraints asserted by `tests/test_after6_logo_svg.py`
- Produces: a standalone SVG whose public accessible name is `After6`

- [ ] **Step 1: Create the minimal SVG implementation**

Create `output/after6-logo-white.svg` with this complete content:

```svg
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 1500 520"
     role="img"
     aria-label="After6"
     preserveAspectRatio="xMidYMid meet">
  <title>After6 white vector logo</title>

  <g id="after6-emblem" fill="#fff">
    <path d="M108 388 A176 176 0 0 1 230 84"
          fill="none" stroke="#fff" stroke-width="24" stroke-linecap="butt"/>
    <path d="M345 88 A176 176 0 0 1 432 401"
          fill="none" stroke="#fff" stroke-width="24" stroke-linecap="butt"/>
    <polygon points="158,438 298,56 356,56 222,438"/>
    <polygon points="298,56 356,56 356,258 309,229"/>
    <polygon points="204,273 338,273 338,239 430,306 338,373 338,338 180,338"/>
  </g>

  <g id="after6-wordmark" fill="#fff">
    <path d="M492 408 V211 Q492 145 560 145 H635 V190 H575 Q541 190 541 220 V247 H625 V291 H541 V408 Z"/>
    <path d="M665 174 H716 V247 H774 V291 H716 V342 Q716 369 746 369 H778 V412 H735 Q665 412 665 345 V291 H638 V247 H665 Z"/>
    <path fill-rule="evenodd" d="M802 319 Q802 238 891 238 Q981 238 981 323 V344 H853 Q860 374 900 374 H972 V414 H893 Q802 414 802 319 Z M853 306 H932 Q927 277 893 277 Q860 277 853 306 Z"/>
    <path d="M1008 247 H1058 V280 Q1085 238 1133 238 H1156 V286 H1133 Q1058 286 1058 349 V414 H1008 Z"/>
    <path fill-rule="evenodd" d="M1248 220 H1362 V264 H1277 Q1238 264 1228 307 Q1256 286 1305 286 Q1376 286 1376 351 Q1376 418 1295 418 Q1179 418 1179 327 Q1179 220 1248 220 Z M1228 353 Q1228 381 1294 381 Q1328 381 1328 352 Q1328 324 1293 324 Q1245 324 1228 353 Z"/>
  </g>
</svg>
```

- [ ] **Step 2: Run the focused test**

Run: `uv run pytest -q tests/test_after6_logo_svg.py`

Expected: `1 passed`.

- [ ] **Step 3: Run XML parsing and prohibited-element checks directly**

Run:

```powershell
uv run python -c "from pathlib import Path; from xml.etree import ElementTree as ET; p=Path('output/after6-logo-white.svg'); r=ET.parse(p).getroot(); s=p.read_text(encoding='utf-8').lower(); assert r.attrib['aria-label']=='After6'; assert all(x not in s for x in ('<image','<text','<rect','gradient','<filter')); print('svg-contract-ok')"
```

Expected: `svg-contract-ok`.

- [ ] **Step 4: Commit the SVG asset**

```powershell
git add -- output/after6-logo-white.svg
git commit -m "feat: add After6 white vector logo"
```

---

### Task 3: Render and visually verify the vector asset

**Files:**
- Verify: `output/after6-logo-white.svg`
- Generate locally: `output/after6-logo-white-preview.png` (ignored verification artifact)
- Test: `tests/test_after6_logo_svg.py`

**Interfaces:**
- Consumes: `output/after6-logo-white.svg`
- Produces: rendered evidence that the SVG is legible, balanced, and actually transparent

- [ ] **Step 1: Render the SVG with an alpha channel**

Run:

```powershell
uv run python -c "import fitz; from pathlib import Path; src=Path('output/after6-logo-white.svg').read_bytes(); doc=fitz.open(stream=src,filetype='svg'); pix=doc[0].get_pixmap(matrix=fitz.Matrix(2,2),alpha=True); pix.save('output/after6-logo-white-preview.png'); print(pix.width,pix.height,pix.alpha)"
```

Expected: output ending in `1`, indicating an alpha channel, and a PNG at `output/after6-logo-white-preview.png`.

- [ ] **Step 2: Inspect the preview visually**

Open `output/after6-logo-white-preview.png` with the local image viewer. Confirm:

- the emblem reads as the capital A;
- the combined mark reads exactly `After6`;
- `fter` remains lowercase;
- the numeral 6 is optically balanced rather than oversized;
- the A emblem and wordmark share a baseline and have a clean gap;
- curves and diagonal edges are smooth;
- the area outside the white mark is transparent.

- [ ] **Step 3: Run the focused and full test suites**

Run:

```powershell
uv run pytest -q tests/test_after6_logo_svg.py
uv run pytest -q
```

Expected: focused test passes; the full pre-existing project suite remains green.

- [ ] **Step 4: Check repository hygiene**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; the ignored PNG preview may remain untracked/ignored, and no unrelated user files are staged.

- [ ] **Step 5: Make a verification-only adjustment if required**

If visual inspection finds imbalance, edit only the affected coordinates in `output/after6-logo-white.svg`, rerun Steps 1–4, and commit only the SVG:

```powershell
git add -- output/after6-logo-white.svg
git commit -m "fix: refine After6 logo geometry"
```

If no adjustment is required, do not create an empty commit.
