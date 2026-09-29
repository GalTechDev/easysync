// Tailwind config partagée — reprise du portfolio (bio.galtech.cc)
tailwind.config = {
    darkMode: "class",
    theme: {
        extend: {
            colors: {
                "primary": "#0056c6",
                "primary-container": "#006df8",
                "on-primary": "#ffffff",
                "on-primary-container": "#fefcff",
                "secondary": "#006e0b",
                "secondary-container": "#4efd4a",
                "tertiary": "#545c72",
                "outline": "#727787",
                "outline-variant": "#c2c6d8",
                "error": "#ba1a1a",
                "error-container": "#ffdad6",
                "background": "#f7f9fb",
                "on-background": "#191c1e",
                "surface": "#f7f9fb",
                "surface-bright": "#f7f9fb",
                "surface-container-lowest": "#ffffff",
                "surface-container-low": "#f2f4f6",
                "surface-container": "#eceef0",
                "surface-container-high": "#e6e8ea",
                "surface-container-highest": "#e0e3e5",
                "on-surface": "#191c1e",
                "on-surface-variant": "#424655",
                "inverse-surface": "#2d3133"
            },
            borderRadius: {
                "DEFAULT": "0.125rem",
                "lg": "0.25rem",
                "xl": "0.5rem",
                "full": "0.75rem"
            },
            spacing: {
                "base": "8px",
                "gutter": "24px",
                "section-padding": "80px",
                "container-max": "1280px",
                "margin-mobile": "16px"
            },
            fontFamily: {
                "body-base": ["Inter"],
                "body-sm": ["Inter"],
                "display-lg": ["JetBrains Mono"],
                "headline-md": ["JetBrains Mono"],
                "code-snippet": ["JetBrains Mono"],
                "label-caps": ["JetBrains Mono"]
            },
            fontSize: {
                "body-base": ["16px", { lineHeight: "1.6", fontWeight: "400" }],
                "body-sm": ["14px", { lineHeight: "1.5", fontWeight: "400" }],
                "display-lg": ["48px", { lineHeight: "1.1", letterSpacing: "-0.04em", fontWeight: "700" }],
                "display-lg-mobile": ["32px", { lineHeight: "1.2", fontWeight: "700" }],
                "headline-md": ["24px", { lineHeight: "1.3", fontWeight: "600" }],
                "code-snippet": ["13px", { lineHeight: "1.6", fontWeight: "400" }],
                "label-caps": ["12px", { lineHeight: "1", letterSpacing: "0.1em", fontWeight: "600" }]
            }
        }
    }
};
