#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade desktop launcher install launcher workflow.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNNER="$ROOT_DIR/scripts/desktop/run.sh"
APP_NAME="NeuroCade"

case "$(uname -s 2>/dev/null || true)" in
  Linux)
    desktop_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    install -d "$desktop_dir"
    desktop_file="$desktop_dir/neurocade.desktop"
    cat >"$desktop_file" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Local NeuroCade desktop launcher
Exec=$RUNNER
Icon=$ROOT_DIR/client/electron/assets/icon.png
Terminal=false
Categories=Science;Education;
StartupNotify=true
StartupWMClass=$APP_NAME
EOF
    chmod 755 "$desktop_file"
    echo "Installed desktop launcher: $desktop_file"
    ;;
  Darwin)
    launcher_dir="$HOME/Applications"
    mkdir -p "$launcher_dir"
    app_bundle="$launcher_dir/NeuroCade.app"
    electron_app="$ROOT_DIR/client/node_modules/electron/dist/Electron.app"
    if [[ -d "$electron_app" ]]; then
      rm -rf "$app_bundle"
      cp -R "$electron_app" "$app_bundle"
      app_contents="$app_bundle/Contents"
      app_resources="$app_contents/Resources"
      cp "$ROOT_DIR/client/electron/assets/icon.icns" "$app_resources/icon.icns"
      if [[ -f "$app_contents/MacOS/Electron" ]]; then
        mv "$app_contents/MacOS/Electron" "$app_contents/MacOS/$APP_NAME"
      fi
      /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $APP_NAME" "$app_contents/Info.plist"
      /usr/libexec/PlistBuddy -c "Set :CFBundleExecutable $APP_NAME" "$app_contents/Info.plist"
      /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile icon.icns" "$app_contents/Info.plist"
      /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier org.neurocade.app" "$app_contents/Info.plist"
      /usr/libexec/PlistBuddy -c "Set :CFBundleName $APP_NAME" "$app_contents/Info.plist"
      /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 1.0" "$app_contents/Info.plist"
      /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 1" "$app_contents/Info.plist"
      rm -rf "$app_resources/app"
      mkdir -p "$app_resources/app"
      printf '%s\n' "$ROOT_DIR" >"$app_resources/app/neurocade-root.txt"
      cat >"$app_resources/app/package.json" <<EOF
{
  "name": "neurocade",
  "productName": "$APP_NAME",
  "version": "1.0.0",
  "main": "main.cjs"
}
EOF
      cat >"$app_resources/app/main.cjs" <<'EOF'
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const root = fs.readFileSync(path.join(__dirname, 'neurocade-root.txt'), 'utf8').trim();
const mainPath = path.join(root, 'client', 'electron', 'main.mjs');

import(pathToFileURL(mainPath).href).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
EOF
      echo "Installed macOS app launcher: $app_bundle"
      exit 0
    fi

    app_contents="$app_bundle/Contents"
    app_macos="$app_contents/MacOS"
    app_resources="$app_contents/Resources"
    mkdir -p "$app_macos" "$app_resources"
    cp "$ROOT_DIR/client/electron/assets/icon.icns" "$app_resources/icon.icns"
    cat >"$app_contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIconFile</key>
  <string>icon.icns</string>
  <key>CFBundleIdentifier</key>
  <string>org.neurocade.app</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
</dict>
</plist>
EOF
    cat >"$app_macos/$APP_NAME" <<EOF
#!/usr/bin/env bash
exec "$RUNNER"
EOF
    chmod 755 "$app_macos/$APP_NAME"
    echo "Installed macOS app launcher: $app_bundle"
    ;;
  *)
    echo "Desktop shortcut creation is not supported on this OS."
    echo "Use: $RUNNER"
    ;;
esac
