/** Pages that extend base.html. Theme = the former inline `tailwind.config` of base.html (keep them in sync). */
const palette = {
    50: '#f1f3fe', 100: '#e4e8fd', 200: '#ced5fc', 300: '#aab5f9', 400: '#7e8cf6', 500: '#5a6bf3',
    600: '#4154f1', 700: '#3342d4', 800: '#2c39ab', 900: '#273487', 950: '#181f52',
};

module.exports = {
    content: [
        '../employees/templates/**/*.html',
        '!../employees/templates/login.html',
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: ['Manrope', 'system-ui', 'sans-serif'],
                display: ['IBM Plex Sans', 'system-ui', 'sans-serif'],
            },
            colors: {
                ink: '#1f2937',
                shell: '#f4f6fb',
                accent: '#eb6834',
                cobalt: '#4154f1',
                panel: '#ffffff',
                // The site's main colour #4154f1 (600): every blue-* and violet-* class uses this scale.
                blue: palette,
                violet: palette,
            },
            boxShadow: {
                panel: '0 24px 60px rgba(15, 23, 42, 0.08)',
            },
        },
    },
};
