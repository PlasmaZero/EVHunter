import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
process.chdir(root);
const python = process.env.PYTHON_EXE || (process.platform === "win32" ? "py" : "python3");
function run(command, args) { const result = spawnSync(command, args, { stdio: "inherit" }); if (result.error || result.status !== 0) { console.error("Setup failed. See the manual setup steps in README.md."); process.exit(1); } }
if (!existsSync(".venv")) run(python, ["-m", "venv", ".venv"]);
const local = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
run(local, ["-m", "pip", "install", "-c", "backend/requirements.lock", "-e", "./backend[dev]"]);
if (!existsSync(".env")) copyFileSync(".env.example", ".env");
console.log("Setup complete. Run npm run dev, then open http://localhost:3000. Local demo: daniel / evhunter-demo-2026");
