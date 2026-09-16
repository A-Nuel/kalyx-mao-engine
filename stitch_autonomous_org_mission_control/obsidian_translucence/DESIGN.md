---
name: Obsidian Translucence
colors:
  surface: '#111318'
  surface-dim: '#111318'
  surface-bright: '#37393e'
  surface-container-lowest: '#0c0e12'
  surface-container-low: '#1a1c20'
  surface-container: '#1e2024'
  surface-container-high: '#282a2e'
  surface-container-highest: '#333539'
  on-surface: '#e2e2e8'
  on-surface-variant: '#c2c6d6'
  inverse-surface: '#e2e2e8'
  inverse-on-surface: '#2f3035'
  outline: '#8c909f'
  outline-variant: '#424754'
  surface-tint: '#adc6ff'
  primary: '#adc6ff'
  on-primary: '#002e6a'
  primary-container: '#4d8eff'
  on-primary-container: '#00285d'
  inverse-primary: '#005ac2'
  secondary: '#b7c8e1'
  on-secondary: '#213145'
  secondary-container: '#3a4a5f'
  on-secondary-container: '#a9bad3'
  tertiary: '#b4c5ff'
  on-tertiary: '#002a78'
  tertiary-container: '#618bff'
  on-tertiary-container: '#002469'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#adc6ff'
  on-primary-fixed: '#001a42'
  on-primary-fixed-variant: '#004395'
  secondary-fixed: '#d3e4fe'
  secondary-fixed-dim: '#b7c8e1'
  on-secondary-fixed: '#0b1c30'
  on-secondary-fixed-variant: '#38485d'
  tertiary-fixed: '#dbe1ff'
  tertiary-fixed-dim: '#b4c5ff'
  on-tertiary-fixed: '#00174b'
  on-tertiary-fixed-variant: '#003ea8'
  background: '#111318'
  on-background: '#e2e2e8'
  surface-variant: '#333539'
typography:
  display-xl:
    fontFamily: Geist
    fontSize: 48px
    fontWeight: '600'
    lineHeight: 56px
    letterSpacing: -0.03em
  display-xl-mobile:
    fontFamily: Geist
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Geist
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Geist
    fontSize: 22px
    fontWeight: '500'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Geist
    fontSize: 18px
    fontWeight: '500'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Geist
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: -0.005em
  body-md:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  body-sm:
    fontFamily: Geist
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0.005em
  label-lg:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
    letterSpacing: 0.02em
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
    letterSpacing: 0.03em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.06em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1.25rem
  gutter-sm: 0.75rem
  gutter-lg: 1.75rem
  margin: 2rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
---

## Brand & Style

This design system establishes an executive command center environment for autonomous governance, high-velocity infrastructure telemetry, and automated operations. The aesthetic merges Apple-grade dark glassmorphism with architectural minimalism: deep void blacks, translucent slate surfaces, micro-refined borders, and precision typographic hierarchy.

The visual experience avoids noisy cyber tropes, neon accents, and decorative visual debt. It prioritizes:
- **Restraint and Focus:** The canvas recedes into pitch-black depths, allowing real-time autonomous systems data, status rings, and controls to float forward naturally.
- **Architectural Tactility:** Subtle optical refraction, calibrated backdrop blurs, and hairline perimeter borders give dark glass panels a physical, machined presence.
- **Cognitive Clarity:** High-density telemetry sits comfortably against low-luminance neutral slate values, mitigating fatigue during prolonged monitoring cycles.

## Colors

The chromatic architecture rests on deep obsidian tones accented by crystalline slate neutrals and a measured cobalt blue.

### Foundations & Surfaces
- **Canvas Base:** `#07080B` represents the bedrock canvas void.
- **Base Canvas Elevated:** `#0A0C10` defines underlying workspace regions and global margins.
- **Frosted Dark Glass (Base Layer):** `rgba(17, 19, 26, 0.72)` with standard backdrop-filter blur of `20px` to `32px`.
- **Frosted Dark Glass (Elevated Modal / Overlay):** `rgba(24, 27, 36, 0.85)` with blur `40px`.
- **Panel Borders (Inner & Outer):** Hairline `rgba(255, 255, 255, 0.08)` for standard card surfaces; `rgba(255, 255, 255, 0.14)` for active or hovered nodes.

### Content & Metadata Spectrum
- **High-Emphasized Text:** `#FFFFFF` — clean, non-compromised foreground for primary metrics, titles, and structural labels.
- **Standard Body & Data Values:** `#CBD5E1` (Slate 300) — optimal readability without aggressive glare.
- **Muted Metadata & Structural Hints:** `#94A3B8` (Slate 400).
- **Secondary Metadata, Footers & Disabled:** `#64748B` (Slate 500).

### Accent & Functional Semantics
- **Interactive Blue Primary:** `#3B82F6` (Electric Slate-Blue) for key operational triggers, focus rings, and selection indicators.
- **Action State Hover/Pressed:** `#2563EB` (Deep Cobalt).
- **Subtle Selection Fill:** `rgba(59, 130, 246, 0.12)`.
- **Status Signals:**
  - Success/Nominal: `rgba(52, 211, 153, 0.9)` with faint backing wash `rgba(16, 185, 129, 0.10)`.
  - Warning/Degraded: `rgba(251, 191, 36, 0.9)` with backing wash `rgba(245, 158, 11, 0.10)`.
  - Critical/Interrupted: `rgba(248, 113, 113, 0.9)` with backing wash `rgba(239, 68, 68, 0.10)`.

## Typography

The typographic stack balances Swiss modernist clarity with the precision of engineering instruments.

- **Primary Interface (Headings, Body):** **Geist** delivers crisp geometric shapes, balanced counterforms, and clinical neutral letterforms that keep interfaces legibly unburdened.
- **Telemetry & Technical Annotations:** **JetBrains Mono** enforces structured tabular readouts, code snippets, execution IDs, timestamp markers, and quantitative metrics.
- **Tabular Figures:** All dynamic numerical dashboards must enforce `font-feature-settings: "tnum" 1` to prevent layout shift during variable updates.
- **Scale Hierarchy:** Dense operations rely heavily on `body-md` and `label-md` pairings, keeping visual space available for multidimensional monitoring.

## Layout & Spacing

This design system uses a responsive modular grid designed for density without visual friction.

### Grid Foundations
- **Desktop (1440px+):** 12-column fluid grid, `2rem` page margins, `1.25rem` gutters. Content containers can stretch full-width for command grids or pin to `1680px` max bounds.
- **Tablet / Mid-tier (768px - 1439px):** 8-column layout, `1.5rem` margins, `1rem` gutters. Multi-tier data panels reflow into 2-up or single-column stacked sequences.
- **Mobile (< 768px):** 4-column layout, `1rem` margins, `0.75rem` gutters. Lateral navigation sheets and overlay drawer panels supersede persistent sidebars.

### Spacing Principles
- Internal card padding operates strictly on `space-md` (`1rem`) for telemetry items and `space-lg` (`1.5rem`) for primary workspace consoles.
- Gaps between nested child elements (e.g., metric label to value) follow `space-xs` (`4px`) and `space-sm` (`8px`) for immediate optical association.

## Elevation & Depth

Visual depth is achieved through layered frosted glass and specular rim lighting rather than traditional drop shadows.

### Surface Tiers
1. **Canvas (Depth 0):** Pure `#07080B`, un-blurred, absorbs all ambient elements.
2. **Structural Glass (Depth 1):** Background `rgba(17, 19, 26, 0.72)`, backdrop blur `24px`, saturation `140%`. Encased in a single-pixel perimeter `border: 1px solid rgba(255, 255, 255, 0.07)`.
3. **Elevated Panels & Flyouts (Depth 2):** Background `rgba(22, 25, 35, 0.82)`, backdrop blur `36px`. Hairline border `rgba(255, 255, 255, 0.12)`. Supported by a subtle ambient diffusion: `box-shadow: 0 16px 40px -12px rgba(0, 0, 0, 0.65)`.
4. **Modals & Command Palettes (Depth 3):** Background `rgba(28, 31, 44, 0.90)`, backdrop blur `48px`, bounded by `rgba(255, 255, 255, 0.16)`. Ambient projection: `0 24px 64px -16px rgba(0, 0, 0, 0.85)`.

### Specular Edge Refraction
To achieve Apple-grade glass characteristics, high-priority cards may include an internal top inset highlight: `box-shadow: inset 0 1px 0 0 rgba(255, 255, 255, 0.10)`. No saturated colored glows or fuzzy outer drop shadows are permitted.

## Shapes

The geometric framework applies consistent, smooth corner rounding inspired by Apple's squircle treatments:

- **Base Components (Inputs, Buttons, Badges):** `rounded` (`0.5rem` / `8px`).
- **Surface Cards, Telemetry Tiles & Sections:** `rounded-lg` (`1rem` / `16px`).
- **Modals, Floating Panels & Overlay HUDs:** `rounded-xl` (`1.5rem` / `24px`).
- **System Tags & Status Pills:** Full circular capsule (`9999px`).

Borders always maintain continuous, uniform 1px micro-weights (`border-width: 1px`) regardless of shape scaling to preserve delicate crystalline contours.

## Components

### Buttons
- **Primary:** Background `#3B82F6`, text `#FFFFFF`, font `Geist Medium 14px`. Subtle top highlight `inset 0 1px 0 rgba(255, 255, 255, 0.2)`. Hover: `#2563EB`. Active: transforms to scale `0.98`.
- **Secondary (Glass):** Translucent fill `rgba(255, 255, 255, 0.05)`, border `1px solid rgba(255, 255, 255, 0.10)`, text `#CBD5E1`. Hover: background `rgba(255, 255, 255, 0.09)`, text `#FFFFFF`.
- **Ghost/Icon Action:** Transparent surface, padding `8px`, border `1px solid transparent`. Hover: border `rgba(255, 255, 255, 0.08)`, background `rgba(255, 255, 255, 0.04)`.

### Input Fields & Controls
- **Search & Text Inputs:** Fill `rgba(11, 13, 18, 0.8)`, border `1px solid rgba(255, 255, 255, 0.08)`, font `Geist 14px`, text `#FFFFFF`, placeholder `#64748B`. Focus: border `1px solid #3B82F6`, subtle halo `box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.20)`.
- **Segmented Controls:** Glass container `rgba(255, 255, 255, 0.03)` with `4px` inner padding. Active pill: `rgba(255, 255, 255, 0.12)`, text `#FFFFFF`, with smooth position transition.

### Chips & Status Badges
- **Capsule Structure:** Height `24px`, padding `0 10px`, typography `JetBrains Mono 11px uppercase`.
- **Neutral State:** Fill `rgba(255, 255, 255, 0.04)`, border `1px solid rgba(255, 255, 255, 0.08)`, text `#94A3B8`.
- **Active / Operational:** Fill `rgba(59, 130, 246, 0.10)`, border `1px solid rgba(59, 130, 246, 0.30)`, text `#93C5FD`, paired with a 5px pulsing node dot.

### Cards & Telemetry Containers
- Structural base: Glass base surface tier (`rgba(17, 19, 26, 0.72)` + blur), `16px` inner corner radius, `1px` white stroke (`0.08` opacity).
- Card headers feature clear separation via metadata taglines (`label-sm` uppercase in `#64748B`) followed by prominent white data headlines (`#FFFFFF`).

### Selection Controls
- **Checkboxes & Radios:** Unchecked: `16px` diameter/square, border `1px solid rgba(255, 255, 255, 0.25)`, background `rgba(255, 255, 255, 0.02)`. Checked: `#3B82F6` with crisp white iconography.

### Data Lists & Execution Tables
- Row headers: `JetBrains Mono 11px`, text `#64748B`, letter spacing `0.05em`.
- Alternating row states avoid solid fills; instead, use bottom divider lines `1px solid rgba(255, 255, 255, 0.04)`. Hovering a table row shifts background to `rgba(255, 255, 255, 0.03)`.