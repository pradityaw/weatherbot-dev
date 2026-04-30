/**
 * Pre-live safety review: runs the Cursor agent against the weatherbot repo root.
 *
 * Setup:
 *   Node 22+, then from this directory: npm install
 *   export CURSOR_API_KEY="crsr_..."  # https://cursor.com/dashboard/integrations
 *
 * Run:
 *   npm run prelive
 *
 * Optional — focus the review on your last commit:
 *   npm run prelive -- --since HEAD~1
 */
import { Agent } from "@cursor/sdk";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");

const PRELIVE_PROMPT = `You are doing a **pre-live safety review** for the weatherbot trading project.

**Context (read the actual files; do not assume):**
- Bot entry: \`bot_v2.py\` (started by \`launch-weatherbot.sh\` → \`python bot_v2.py run\`).
- Live guardrails and keys: \`.env.weatherbot-live.example\` (real values should be in \`.env.weatherbot-live\`, not committed).
- Paper vs live toggles often include \`WEATHERBOT_LIVE_TRADING\`, \`WEATHERBOT_DRY_RUN_LIVE\`, caps, kill switch file \`data/pause_live\`.

**Your job — output a short checklist the human can scan before enabling live trading:**

1. **Live vs paper**: Where could real orders still fire if env is wrong? Any default that is unsafe?
2. **Limits**: Are caps / daily loss / max trades enforced in code paths the user will hit?
3. **Secrets**: Any risk of logging keys, printing env, or committing \`.env.weatherbot-live\`?
4. **Execution path**: Trace \`execution.py\` / CLOB-related code: failure modes, dry-run behavior, idempotency.
5. **Operational**: Kill switch file, logs under \`logs/\`, anything that should block go-live.

Be specific: cite file paths and function names. If something is unclear, say what to verify manually (e.g. exact env value).

If the user passed a \`--since\` git ref in the message below, prioritize what changed in that range; otherwise review the whole live-relevant surface.
`;

function main() {
  const extra = process.argv.slice(2).join(" ");
  const since =
    extra.includes("--since") && extra.match(/--since\s+(\S+)/)
      ? extra.match(/--since\s+(\S+)/)![1]
      : null;

  let scope = "";
  if (since) {
    scope = `\n\n**Scope:** Focus on changes since git ref: \`${since}\` (use \`git diff ${since}...HEAD\` or similar mental model; you have full repo access).`;
  }

  return PRELIVE_PROMPT + scope;
}

const prompt = main();

if (!process.env.CURSOR_API_KEY) {
  console.error("CURSOR_API_KEY is not set. See https://cursor.com/dashboard/integrations");
  process.exit(1);
}


await using agent = await Agent.create({
  apiKey: process.env.CURSOR_API_KEY,
  model: { id: "composer-2" },
  local: { cwd: REPO_ROOT },
});

const run = await agent.send(prompt);

for await (const event of run.stream()) {
  if (event.type === "assistant") {
    for (const block of event.message.content) {
      if (block.type === "text") process.stdout.write(block.text);
    }
  }
}

console.log("\n");
