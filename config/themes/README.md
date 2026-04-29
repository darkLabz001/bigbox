# 🎨 DaRkb0x Theme Engine

Welcome to the official theming guide! The DaRkb0x engine is designed to be highly customizable, allowing you to change colors, background images, and icon sets with simple JSON files.

## 📁 Theme Directory Structure
A complete, detailed theme usually consists of a JSON file and an optional assets folder:
```text
config/themes/
├── my_tactical_theme.json      # The main theme definition
└── my_tactical_theme/          # (Optional) Assets folder
    ├── background.png          # Recommended: 800x480
    └── icons/                  # Your custom icon set
        ├── about.png           # 32px - 64px recommended
        ├── bluetooth.png
        ├── media.png
        ├── network.png
        ├── recon.png
        ├── settings.png
        ├── social.png
        └── wireless.png
```

## 📝 The "Master Template" (JSON)
Copy this template into a new file (e.g., `my_theme.json`) to begin.

```json
{
  "meta": {
    "name": "Ghost Protocol",
    "author": "sinXne0",
    "version": "1.0"
  },
  "colors": {
    "bg": "#0a0a0f",           // Main background color
    "bg_alt": "#14141f",       // Headers, status bars, and modal backgrounds
    "fg": "#e0e0e0",           // Primary text color
    "fg_dim": "#808080",       // Secondary/dimmed text (hints, metadata)
    "accent": "#00ffcc",       // Primary tactical color (lines, active buttons)
    "accent_dim": "#006652",   // Dimmed accent (scrollbars, grid lines)
    "selection": "#00ffcc",    // Color of the selected text
    "selection_bg": "#1a332d", // Background behind a selected list item
    "divider": "#2a2a35",      // Thin lines separating UI sections
    "err": "#ff3366",          // Error messages and critical alerts
    "warn": "#ffcc33"          // Warning states (loading, handshake capture)
  },
  "assets": {
    "background": "config/themes/my_theme/background.png", 
    "icons_dir": "config/themes/my_theme/icons/"
  }
}
```

## 🛠️ Anatomy of a Theme

### 1. The Palette
*   **bg/bg_alt**: Use dark values for a "stealth" look. `bg_alt` should be slightly lighter than `bg`.
*   **accent**: This is your signature color. Cyan, Neon Green, and Red are classic tactical choices.
*   **fg_dim**: Crucial for UI clarity. Use this for the "press B to back" hints at the bottom.

### 2. Custom Icons
If you provide an `icons_dir`, the system will search that folder for icons matching the section titles.
*   **Required Filenames**: `about.png`, `bluetooth.png`, `games.png`, `media.png`, `network.png`, `recon.png`, `settings.png`, `social.png`, `wireless.png`.
*   **Format**: PNG with transparency.
*   **Size**: We recommend **64x64px** for high clarity. The system will automatically scale them to fit the carousel.

### 3. Background Image
*   **Size**: **800x480** (GamePi43 native resolution).
*   **Style**: Dark, high-contrast images work best. Subtle grid patterns or abstract data-vis textures give a professional tactical feel.

## 🚀 Installation & Testing
1. Save your JSON file in `config/themes/`.
2. Open the **Settings** menu on your DaRkb0x.
3. Select **Theme Manager**.
4. Your new theme will appear in the list! Select it and press **A** to apply.
