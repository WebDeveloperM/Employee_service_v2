/** login.html (standalone page). Theme = its former inline `tailwind.config` (keep them in sync). */
module.exports = {
    content: ['../employees/templates/login.html'],
    theme: {
        extend: {
            fontFamily: {
                sans: ['Manrope', 'system-ui', 'sans-serif'],
                display: ['IBM Plex Sans', 'system-ui', 'sans-serif'],
            },
            colors: {
                // Same palette as base.html: main colour #4154f1.
                blue: {
                    50: '#f1f3fe', 100: '#e4e8fd', 200: '#ced5fc', 300: '#aab5f9', 400: '#7e8cf6', 500: '#5a6bf3',
                    600: '#4154f1', 700: '#3342d4', 800: '#2c39ab', 900: '#273487', 950: '#181f52',
                },
            },
        },
    },
};
