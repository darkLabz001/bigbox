# DaRkb0x Themes

Welcome to the DaRkb0x themes directory! Here you can create and install custom color palettes to change the look and feel of your device.

## Technical Specifications
- **Screen Resolution:** 800 x 480 pixels.
- **Background Images:** Recommended size is **800 x 480**. The device will "cover-fit" larger or smaller images, but exact sizing prevents cropping.
- **Icons:** Must be in **PNG** format with transparency. The system scales all icons to **28px high**; keep your source icons at least this large for clarity.

## Creating a Theme
1. Copy `template.json` and rename it (e.g., `my_theme.json`).
2. Edit the hex color codes in your new JSON file.
3. **Custom Assets (Optional):**
   - **Background:** Add a path to a custom **800x480** image in the `assets.background` field.
   - **Icons:** To use a custom set of icons, put them in a folder and set `assets.icons_dir`. The device will look for `recon.png`, `settings.png`, etc., in that folder (scaled to **28px** height).
4. Test your theme by copying it to `active.json` in this directory and restarting the DaRkb0x UI (`sudo systemctl restart bigbox`).

## Submitting to the Community Repo
Want to share your theme? Submit a Pull Request to our official community repository!
1. Fork the repo: `https://github.com/darkLabz001/darkbox-themes`
2. Add your `.json` theme file.
3. Submit a Pull Request! Once accepted, users can download it directly from their devices using the Theme Manager.
