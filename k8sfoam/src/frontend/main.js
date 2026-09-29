// esbuild entry for bundle.js. The .jsx files share code through window.*, so
// they are imported in the order index.html used to load them.
import "./globals.js";
import "./nodestatus.jsx";
import "./podaudit.jsx";
import "./qos.jsx";
import "./query.jsx";
import "./workload.jsx";
import "./tweaks-panel.jsx";
import "./treemap.jsx";
import "./scene3d.jsx";
import "./app.jsx";
