# ForgeLM Website

Static marketing site for ForgeLM. Vanilla HTML / CSS / JS — no build step, no framework, no node_modules.

## Local preview

```bash
# any static server works; pick one
python3 -m http.server 8080 --directory site
# or
npx serve site
# or open site/index.html directly in a browser (file://)
```

Then visit <http://localhost:8080/>.

## Structure

```
site/
├── index.html         # landing — hero, pipeline, features, YAML demo, CTA
├── features.html      # full feature matrix (Training / Eval / Data / Enterprise)
├── compliance.html    # EU AI Act Article 9-17 + Annex IV mapping
├── quickstart.html    # 5-step developer onboarding
├── contact.html       # Formspree-backed contact form + channels
├── privacy.html       # privacy policy (EN + TR)
├── terms.html         # terms of use (EN + TR)
├── css/
│   └── style.css      # design tokens, components — dark-first, light variant
├── js/
│   ├── i18n.js        # EN/TR language switcher (localStorage-backed)
│   └── main.js        # nav, theme toggle, copy buttons, form, terminal animation
└── assets/            # (empty — drop OG images / favicons here later)
```

## Design

- **Forge metaphor** — ember orange (`#f97316`) primary, steel cyan (`#0ea5e9`) accent.
- **Dark-first** — light theme available via the moon/sun toggle.
- **Inter** for UI, **JetBrains Mono** for code.
- **Bilingual** — EN / TR via `data-lang` spans + `localStorage`-persisted choice.
- **No tracking** — no analytics, no pixels, no third-party JS beyond Google Fonts and (optionally) the Formspree endpoint.

## Configuration

### 1. Formspree form ID

`contact.html` currently uses the form ID `xnjlejbb`:

```html
<form ... action="https://formspree.io/f/xnjlejbb" method="POST">
```

To use your own Formspree form:

1. Sign up at <https://formspree.io>.
2. Create a new form labelled "ForgeLM contact" — set the destination email there (no email is exposed in the HTML).
3. Replace `xnjlejbb` with your new form ID in `site/contact.html`.

### 2. GitHub repo URL

Search-and-replace `HodeTech/ForgeLM` if you fork the project under a different organisation:

```bash
grep -rl "HodeTech/ForgeLM" site/ | xargs sed -i '' 's|HodeTech/ForgeLM|YOUR_ORG/YourFork|g'
```

### 3. Domain / og-image / favicon

All pages already have canonical, og:image, og:url, og:locale, and favicon tags in place — they use the placeholder `YOUR_DOMAIN`. Once you have a domain, run:

```bash
grep -rl "YOUR_DOMAIN" site/ | xargs sed -i '' 's|https://YOUR_DOMAIN|https://your-actual-domain.com|g'
```

Then:

- Drop a 1200×630 PNG at `site/assets/og.png` for social previews.
- Drop `favicon.ico` and `apple-touch-icon.png` at `site/favicon.ico` and `site/apple-touch-icon.png`.

## Deployment

Any static host works. Pick one:

| Host           | Setup                                                                 |
| -------------- | --------------------------------------------------------------------- |
| GitHub Pages   | Settings → Pages → source: `main` branch, folder: `/site`             |
| Cloudflare Pages | Connect repo, build command empty, output directory: `site`          |
| Netlify        | Drag-and-drop the `site/` folder, or connect repo with `publish=site` |
| Vercel         | Import repo, framework preset: "Other", root directory: `site`        |
| S3 + CloudFront | `aws s3 sync site/ s3://your-bucket --delete`                        |

## Accessibility

- Semantic HTML (`<nav>`, `<header>`, `<section>`, `<article>`, `<footer>`).
- `aria-label` / `aria-expanded` on interactive controls.
- `prefers-reduced-motion` honoured (animations disabled).
- Colour contrast meets WCAG AA on both themes.

## License

Apache 2.0, same as the toolkit. See [`../LICENSE`](../LICENSE).
