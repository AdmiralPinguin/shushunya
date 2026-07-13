# Visual system v0.2

## Intent

The application is an opaque full-screen visual body for
computer-as-Shushunya. It is neither a dashboard placed over Linux nor a
transparent overlay. COSMIC remains behind ordinary native Qt windows until a
later shell-integration phase.

Shushunya uses masculine grammatical gender in Russian: `он`, `пробуждён`,
`одержим`. The name ending does not change that rule. His visible body is now a
living field of original heretical symbols rather than an unreliable generated
portrait of a cat.

## Vector-only contract

Active QML must not load PNG hero art, cat imagery, or the old raster sanctum
panorama. The source pack under `assets/heresy/` contains six scalable SVGs:

- `chaos-star.svg` — the clean eight-rayed outer body of the canonical mark;
- `horus-eye.svg` — the fixed central identity on every display;
- `horned-skull.svg` — peripheral heretical texture;
- `fractured-chaos-seal.svg` — peripheral fracture and convergence;
- `warp-eye.svg` — the retired richer eye retained as a source asset;
- `broken-halo-rune.svg` — interruption, transition and ritual punctuation.

These are not toolbar icons. `LivingSeal` deliberately uses exactly two image
layers: one slowly rotating `chaos-star.svg` and one stationary,
gently-breathing `horus-eye.svg`. The identity never switches to another
mascot. Lesser assets fill the edges at low opacity without competing with the
canonical mark or text.

## Scene grammar

### Presence

The chaos star and Eye of Horus form Shushunya's dominant body. Only the star
rotates; the eye stays fixed and breathes almost imperceptibly. Peripheral
marks and tether-like lines lead toward it. Shushunya's current words are cut
into a restrained lower inscription bounded by bone lines and notches, never
placed in a floating dashboard card.

### Mind

The portrait display is a vertical spine of thought over a subdued watermark
of the same star and Eye of Horus. Activities, agenda items and the one
necessary owner request attach to the spine as inscriptions. Phase is
communicated by the node shape and accent, not by machine status badges. With
two displays, the latest result closes the spine as a final seal.

### Canvas

Results inhabit a reliquary. In landscape, readable chapters occupy the left
side while the canonical star and eye hold the right; in portrait, the mark
moves behind the text. Each result is a chapter separated by ritual geometry
rather than a rounded card. The empty state keeps the same identity and one
quiet line of copy.

### Ambient

Additional displays become a quiet wall of heresy: one partially cropped major
symbol, a deterministic field of smaller marks and extremely slow movement.
Screen ordinal changes the composition without changing its family. There is
no clock, health strip, navigation, or service status. At most one subdued
utterance remains near the lower edge.

## Palette

- Abyss: `#030107`
- Iron: `#120D18`
- Warp violet: `#7B38BD`
- Blood: `#8C1028`
- Tarnished gold: `#C8A45D`
- Bone: `#E8DEC7`
- Living nerve: `#4DEBFF`, used sparingly
- Ash: `#9A8EA2`

The scene should read primarily as abyss, bone and muted violet. Cyan is a
state accent, not a general-purpose neon outline.

## State language

- `idle`: a dim eye, barely perceptible breathing and star drift.
- `thinking`: the star becomes clearer while the fixed eye holds attention.
- `speaking`: the eye breathes around the visible utterance.
- `waiting`: a blood fracture or open eye marks the exact owner request.
- `completed`: bone, gold and cyan converge around the delivered result.
- `failed`: a dim blood fracture accompanies the preserved failure text.

Motion remains inexpensive: transform, opacity and gentle scale. Geometry is
static after construction. Avoid full-screen `layer.enabled`, runtime shader
compilation, continuously repainting `Canvas` items and heavy blur pipelines.
QtSvg may ignore complex SVG filters, so essential hierarchy must survive
without glow effects.

## Responsive composition

Layout uses Qt logical pixels. Role scenes size themselves from their actual
QML item bounds and use viewport dimensions only for orientation and aspect
breakpoints.

- Landscape presence keeps the central idol limited by the short edge; an
  ultrawide display gains peripheral symbols instead of stretching the idol.
- Portrait mind preserves a readable inscription column and scrolls only when
  content genuinely exceeds the safe area.
- Canvas switches between side-by-side and vertical reliquary compositions.
- Ambient has explicit landscape and portrait regression captures.

Every screen owns a safe area. Content scale is derived from the short edge,
clamped between `0.62` and `1.18`, and fitted against the appropriate landscape
or portrait design size. Background geometry bleeds to the physical edge while
text stays inside safe bounds.

## Multi-monitor placement

Each monitor owns an ordinary opaque Qt Quick window and all windows share one
backend state. The current physical layout is intentionally asymmetric:

- 1920x1080 landscape primary at logical `(0,0)`;
- 1080x1920 portrait mind display at logical `(1920,0)`;
- a future third display is discovered dynamically and receives `canvas`.

Qt receives the target `QScreen` and its complete compositor-provided geometry
before the native surface is created or fullscreen is requested. Live
diagnostics compare the expected connector, actual `window.screen()`,
fullscreen state and logical size. A launch is accepted only when the top-level
`placement_ok` gate is true for every window.

## Regression gate

Automated tests decode every SVG through Qt and reject active QML references to
PNG art. Offscreen captures cover all role scenes, empty and overloaded data,
and these responsive formats:

- presence: 1366x768, 1920x1080 and 2560x1080;
- mind: 900x1600 and 1080x1920;
- canvas: 1280x1024, 1920x1080 and 1080x1920;
- ambient: 1920x1080 and 1080x1920.

The offscreen capture path uses `QT_QUICK_BACKEND=software`. It must not set
`QSG_RHI_BACKEND=software`, because `software` is a Qt Quick adaptation rather
than a valid RHI backend.
