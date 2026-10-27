import type {
  ExtensionAPI,
  ExtensionContext,
  BeforeAgentStartEvent,
  BeforeAgentStartEventResult,
  ToolCallEvent,
  ToolCallEventResult,
  CustomMessagePayload,
} from "@oh-my-pi/pi-coding-agent";
import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import {
  loadCatalog,
  selectSkills,
  type Catalog,
  type Coverage,
  type RoutingState,
  type Question,
  type Answers,
  type SkillSource,
  type JudgeCall,
} from "../lib/skill-router-core.js";

/**
 * Discover skill sources from .agents/skills in cwd or parent paths.
 */
export async function discoverSkillSources(baseCwd: string): Promise<SkillSource[]> {
  const candidates = [
    join(baseCwd, ".agents", "skills"),
    join(baseCwd, "..", ".agents", "skills"),
  ];

  let skillsDir: string | null = null;
  for (const c of candidates) {
    try {
      const s = await stat(c);
      if (s.isDirectory()) {
        skillsDir = c;
        break;
      }
    } catch {
      // Path does not exist; check next candidate
    }
  }

  if (!skillsDir) {
    throw new Error(`Cannot locate .agents/skills/ directory from ${baseCwd}`);
  }

  const entries = await readdir(skillsDir, { withFileTypes: true });
  const sources: SkillSource[] = [];

  for (const ent of entries) {
    if (!ent.isDirectory() || !ent.name.startsWith("ml4t-")) {
      continue;
    }
    const skillPath = join(skillsDir, ent.name, "SKILL.md");
    try {
      const content = await readFile(skillPath, "utf-8");
      const match = content.match(/^---\s*\n([\s\S]*?)\n---/);
      if (!match) continue;
      const yaml = match[1];
      const descMatch = yaml.match(/^description:\s*(?:["']?)(.*?)(?:["']?)$/m);
      const nameMatch = yaml.match(/^name:\s*(?:["']?)(.*?)(?:["']?)$/m);
      const name = nameMatch ? nameMatch[1].trim() : ent.name;
      const description = descMatch ? descMatch[1].trim() : "";
      sources.push({
        name,
        description,
        filePath: skillPath,
        baseDir: join(skillsDir, ent.name),
      });
    } catch {
      // Skip unreadable or corrupted files
    }
  }

  if (sources.length === 0) {
    throw new Error(`No valid ml4t-* skills found in ${skillsDir}`);
  }

  return sources.sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * Execute typed Jev judgment through TypeSafe System One HTTP API.
 */
export async function executeJudge(
  state: RoutingState,
  questions: Record<string, Question>
): Promise<Answers> {
  const apiKey = process.env.TYPESAFE_API_KEY;
  if (!apiKey) {
    throw new Error(
      "Skill router: No judge available. Configure TYPESAFE_API_KEY or modelRoles.judge."
    );
  }

  const noulQuestions: Record<string, unknown> = {};
  for (const [k, q] of Object.entries(questions)) {
    noulQuestions[k] = {
      type: "noul",
      instructions: q.instructions,
      criteria: q.criteria,
    };
  }

  const res = await fetch("https://api.typesafe.ai/v1/systemone", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "jev-latest",
      state: JSON.stringify(state),
      questions: noulQuestions,
    }),
  });

  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`TypeSafe API error (${res.status}): ${txt}`);
  }

  const raw = (await res.json()) as unknown;
  if (!raw || typeof raw !== "object" || !("answers" in raw)) {
    throw new Error("TypeSafe API returned invalid response shape (missing answers object)");
  }

  const typedAnswers = raw.answers;
  if (!typedAnswers || typeof typedAnswers !== "object") {
    throw new Error("TypeSafe API returned invalid answers format");
  }

  const answers: Answers = {};
  for (const [k, ans] of Object.entries(typedAnswers)) {
    if (ans && typeof ans === "object" && "noul" in ans) {
      const noulVal = ans.noul;
      if (typeof noulVal === "number" && Number.isFinite(noulVal)) {
        answers[k] = { type: "bool", bool: noulVal };
      }
    }
  }

  return answers;
}

export interface SkillRouterOptions {
  judge?: JudgeCall;
  sources?: SkillSource[];
}

/**
 * Main OMP extension factory.
 */
export default function skillRouterExtension(
  pi: ExtensionAPI,
  options?: SkillRouterOptions
): void {
  pi.setLabel("ML4T Skill Router");

  let cachedCatalog: Catalog | null = null;
  let lastCoverage: Coverage | null = null;
  let lastRoutingError: string | null = null;

  const judgeFn: JudgeCall = options?.judge ?? ((st, qs) => executeJudge(st, qs));

  pi.on(
    "before_agent_start",
    async (
      event: BeforeAgentStartEvent,
      ctx: ExtensionContext
    ): Promise<BeforeAgentStartEventResult | undefined> => {
      // Subagent revive turn or empty prompt: do not re-run full routing
      if (event.reason === "revive") {
        return undefined;
      }
      const prompt = event.prompt?.trim();
      if (!prompt) {
        return undefined;
      }

      try {
        if (!cachedCatalog) {
          const sources = options?.sources ?? (await discoverSkillSources(ctx.cwd));
          cachedCatalog = await loadCatalog(sources);
        }

        const routingState: RoutingState = {
          intent: prompt,
          context: `cwd: ${ctx.cwd}, agent: ${ctx.agent?.name ?? "main"} (${ctx.agent?.kind ?? "main"})`,
        };

        const coverage = await selectSkills(cachedCatalog, routingState, judgeFn);
        lastCoverage = coverage;
        lastRoutingError = null;

        try {
          pi.appendEntry("skill_coverage", {
            selected: coverage.selected,
            ambiguous: coverage.ambiguous,
            fingerprint: coverage.fingerprint,
          });
        } catch {
          // Safe ignore if session storage not yet active
        }

        if (coverage.selected.length > 0 && coverage.content.trim()) {
          const message: CustomMessagePayload = {
            customType: "skill",
            content: coverage.content,
            display: false,
            attribution: "agent",
          };
          return { message };
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        lastRoutingError = message;
        try {
          ctx.ui?.notify(`Skill Router error: ${lastRoutingError}`, "warn");
        } catch {
          // Ignore notification errors in headless mode
        }
      }

      return undefined;
    }
  );

  pi.on(
    "tool_call",
    async (
      event: ToolCallEvent,
      _ctx: ExtensionContext
    ): Promise<ToolCallEventResult | undefined> => {
      const isMutation = event.toolName === "write" || event.toolName === "edit";
      let isWardenRun = false;
      if (event.toolName === "bash" && event.input && typeof event.input === "object" && "command" in event.input) {
        const cmd = typeof event.input.command === "string" ? event.input.command : "";
        isWardenRun = /warden\s+(backtest|screen|qualify|verify)/.test(cmd);
      }

      // Fail closed: if there was an active routing error, pause code mutation and research runs
      if ((isMutation || isWardenRun) && lastRoutingError) {
        return {
          block: true,
          reason: `Skill router blocked action: unresolved skill coverage (${lastRoutingError}). Resolve skill requirements before mutating code or running evaluations.`,
        };
      }

      return undefined;
    }
  );
}
