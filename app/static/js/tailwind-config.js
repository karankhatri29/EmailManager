// Tailwind (CDN) configuration. The slate / blue / red / emerald / amber / violet palettes are remapped onto
// CSS variables defined in css/theme.css, so every existing utility class (including the ones built in
// JavaScript) follows the light / dark theme and the chosen accent colour without any per-class overrides.
//
//   slate   -> the neutral scale (backgrounds, borders, text)
//   blue    -> the user's accent colour ("brand")
//   fg      -> the strongest text colour (white in dark mode, near-black in light mode)
(function () {
    const SHADES = [200, 300, 400, 500, 600, 700, 900];

    const hue = (name) => Object.fromEntries(
        SHADES.map((shade) => [shade, `hsl(var(--h-${name}) var(--s-${name}) var(--l-${shade}) / <alpha-value>)`]),
    );
    const slate = Object.fromEntries(
        [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((shade) => [shade, `rgb(var(--slate-${shade}) / <alpha-value>)`]),
    );

    tailwind.config = {
        theme: {
            extend: {
                fontFamily: { sans: ['"Plus Jakarta Sans"', 'Inter', 'system-ui', 'sans-serif'] },
                colors: {
                    slate,
                    blue: hue('brand'),
                    red: hue('red'),
                    emerald: hue('emerald'),
                    amber: hue('amber'),
                    violet: hue('violet'),
                    fg: 'rgb(var(--fg) / <alpha-value>)',
                },
            },
        },
    };
})();
