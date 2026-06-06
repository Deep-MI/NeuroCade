/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                background: '#000000', // Deep black
                surface: '#121212',    // Near black
                panel: '#1e1e1e',      // Dark grey panel
                primary: '#0ea5e9',    // Medical blue
                secondary: '#64748b',  // Muted slate
                accent: '#06b6d4',     // Cyan
                danger: '#dc2626',     // Medical red
                success: '#16a34a',    // Medical green
                gold: '#eab308'        // Attention/Warning
            },
        },
    },
    plugins: [],
}
