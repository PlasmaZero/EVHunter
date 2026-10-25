import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
if (existsSync(path.join(root, ".env"))) process.loadEnvFile(path.join(root, ".env"));
const localPython = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const python = process.env.PYTHON_EXE || (existsSync(localPython) ? localPython : process.platform === "win32" ? "python" : "python3");
const apiPort = process.env.API_PORT || "8000";
const args = process.argv.slice(2);
const portIndex = args.indexOf("--port");
const webPort = portIndex >= 0 ? args[portIndex + 1] : process.env.PORT || "3000";
const env = { ...process.env, BACKEND_URL: process.env.BACKEND_URL || `http://127.0.0.1:${apiPort}` };
const children = [];
let stopping = false;
function stop(code = 0) { if (stopping) return; stopping = true; for (const child of children) child.kill("SIGTERM"); setTimeout(() => process.exit(code), 700).unref(); }
function start(command, args, options) {
  const child = spawn(command, args, { stdio: "inherit", env, ...options });
  child.on("error", () => { console.error(`Could not start ${command}. Run the setup steps in README.md.`); stop(1); });
  child.on("exit", code => { if (!stopping) stop(code || 0); });
  children.push(child);
}
start(python, ["-m", "uvicorn", "evhunter.api:app", "--host", "127.0.0.1", "--port", apiPort], { cwd: path.join(root, "backend") });
start(process.execPath, [path.join(root, "node_modules/next/dist/bin/next"), "dev", "--webpack", "--hostname", "0.0.0.0", "--port", webPort], { cwd: path.join(root, "apps/web") });
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
