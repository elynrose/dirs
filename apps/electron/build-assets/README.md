# build-assets

Place platform icon files here. electron-builder picks them up automatically.

| File | Format | Size | Used for |
|------|--------|------|----------|
| `icon.icns` | Apple Icon Image | 1024×1024 (includes all sub-sizes) | macOS .app + DMG |
| `icon.ico` | Windows Icon | multi-size (16/32/48/64/128/256 px) | Windows installer + taskbar |
| `icon.png` | PNG | 512×512 minimum | Linux AppImage |

## Generating icons from a master PNG

```bash
# macOS — requires Xcode Command Line Tools
mkdir icon.iconset
sips -z 1024 1024 master-1024.png --out icon.iconset/icon_512x512@2x.png
sips -z 512  512  master-1024.png --out icon.iconset/icon_512x512.png
sips -z 256  256  master-1024.png --out icon.iconset/icon_256x256.png
sips -z 128  128  master-1024.png --out icon.iconset/icon_128x128.png
sips -z 64   64   master-1024.png --out icon.iconset/icon_32x32@2x.png
sips -z 32   32   master-1024.png --out icon.iconset/icon_32x32.png
sips -z 16   16   master-1024.png --out icon.iconset/icon_16x16.png
iconutil -c icns icon.iconset -o icon.icns

# Windows .ico — use ImageMagick
magick convert master-1024.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico
```

Until real icons are added, electron-builder uses the default Electron icon.
