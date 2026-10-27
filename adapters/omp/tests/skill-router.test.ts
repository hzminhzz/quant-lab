import { describe, expect, it } from "bun:test";
import { join } from "node:path";
import skillRouterExtension, {
  discoverSkillSources,
} from "../.omp/extensions/skill-router.js";
import type {
  ExtensionAPI,
  ExtensionContext,
  BeforeAgentStartEvent,
  ToolCallEvent,
  CustomMessagePayload,
} from "@oh-my-pi/pi-coding-agent";
import type { Answers, Question, RoutingState, SkillSource } from "../.omp/lib/skill-router-core.js";

describe("skill-router extension integration", () => {
  it("discovers skill sources from repo root .agents/skills", async () => {
    const cwd = join(import.meta.dir, "../../..");
    const sources = await discoverSkillSources(cwd);
    expect(sources.length).toBe(61);
    expect(sources.some((s) => s.name === "ml4t-compute-features")).toBe(true);
    expect(sources.some((s) => s.name === "ml4t-purging-embargo")).toBe(true);
  });

  it("handles before_agent_start and injects selected skills", async () => {
    const handlers: Record<string, Function> = {};
    const appendedEntries: Array<{ type: string; data: unknown }> = [];

    const mockPi: Partial<ExtensionAPI> = {
      setLabel: () => {},
      on: (event: string, handler: Function) => {
        handlers[event] = handler;
      },
      appendEntry: (type: string, data: unknown) => {
        appendedEntries.push({ type, data });
      },
    };

    const mockJudge = async (
      _state: RoutingState,
      questions: Record<string, Question>
    ): Promise<Answers> => {
      const ans: Answers = {};
      for (const k of Object.keys(questions)) {
        if (k === "ml4t-compute-features") {
          ans[k] = { type: "bool", bool: 0.95 };
        } else {
          ans[k] = { type: "bool", bool: 0.05 };
        }
      }
      return ans;
    };

    const cwd = join(import.meta.dir, "../../..");
    skillRouterExtension(mockPi as ExtensionAPI, { judge: mockJudge });

    expect(typeof handlers.before_agent_start).toBe("function");
    expect(typeof handlers.tool_call).toBe("function");

    const mockContext: Partial<ExtensionContext> = {
      cwd,
      agent: { kind: "main", id: "1", name: "main", depth: 0 },
      ui: { notify: () => {} } as any,
    };

    const event: BeforeAgentStartEvent = {
      type: "before_agent_start",
      reason: "prompt",
      prompt: "Engineer rolling momentum features with Polars.",
      systemPrompt: [],
    };

    const result = await handlers.before_agent_start(event, mockContext);
    expect(result).toBeDefined();
    expect(result.message).toBeDefined();
    expect(result.message.customType).toBe("skill");
    expect(result.message.content).toContain("ml4t-compute-features");
    expect(result.message.content).toContain("ml4t-lookahead-bias"); // transitive dependency
    expect(appendedEntries.length).toBe(1);
    expect(appendedEntries[0].type).toBe("skill_coverage");
  });

  it("skips subagent revive turn without running routing", async () => {
    const handlers: Record<string, Function> = {};
    const mockPi: Partial<ExtensionAPI> = {
      setLabel: () => {},
      on: (event: string, handler: Function) => {
        handlers[event] = handler;
      },
      appendEntry: () => {},
    };

    let judgeCalled = false;
    const mockJudge = async (): Promise<Answers> => {
      judgeCalled = true;
      return {};
    };

    skillRouterExtension(mockPi as ExtensionAPI, { judge: mockJudge });

    const event: BeforeAgentStartEvent = {
      type: "before_agent_start",
      reason: "revive",
      prompt: "",
      systemPrompt: [],
    };

    const result = await handlers.before_agent_start(event, { cwd: "." });
    expect(result).toBeUndefined();
    expect(judgeCalled).toBe(false);
  });

  it("blocks mutating actions when routing error occurs", async () => {
    const handlers: Record<string, Function> = {};
    const mockPi: Partial<ExtensionAPI> = {
      setLabel: () => {},
      on: (event: string, handler: Function) => {
        handlers[event] = handler;
      },
      appendEntry: () => {},
    };

    const failingJudge = async (): Promise<Answers> => {
      throw new Error("Provider rate limit 429");
    };

    skillRouterExtension(mockPi as ExtensionAPI, { judge: failingJudge });

    const cwd = join(import.meta.dir, "../../..");
    const mockContext: Partial<ExtensionContext> = {
      cwd,
      agent: { kind: "main", id: "1", name: "main", depth: 0 },
      ui: { notify: () => {} } as any,
    };

    // Prompt fails routing
    await handlers.before_agent_start(
      {
        type: "before_agent_start",
        reason: "prompt",
        prompt: "Write a strategy module",
        systemPrompt: [],
      },
      mockContext
    );

    // Mutation tool call must be blocked
    const toolCallEvent: ToolCallEvent = {
      type: "tool_call",
      toolCallId: "call_1",
      toolName: "write",
      input: { path: "strategies/candidates/test.py", content: "..." },
    };

    const blockResult = await handlers.tool_call(toolCallEvent, mockContext);
    expect(blockResult).toBeDefined();
    expect(blockResult.block).toBe(true);
    expect(blockResult.reason).toContain("unresolved skill coverage");

    // Read tool call is NOT blocked
    const readCallEvent: ToolCallEvent = {
      type: "tool_call",
      toolCallId: "call_2",
      toolName: "read",
      input: { path: "README.md" },
    };
    const readResult = await handlers.tool_call(readCallEvent, mockContext);
    expect(readResult).toBeUndefined();
  });

  it("stale-scope gate blocks strategy mutation and backtest execution when required skills are missing from coverage", async () => {
    const handlers: Record<string, Function> = {};
    const mockPi: Partial<ExtensionAPI> = {
      setLabel: () => {},
      on: (event: string, handler: Function) => {
        handlers[event] = handler;
      },
      appendEntry: () => {},
    };

    // Mock judge that selects zero skills (docs task: low probability for all skills)
    const docsJudge = async (
      _st: RoutingState,
      questions: Record<string, Question>
    ): Promise<Answers> => {
      const ans: Answers = {};
      for (const k of Object.keys(questions)) {
        ans[k] = { type: "bool", bool: 0.05 };
      }
      return ans;
    };
    skillRouterExtension(mockPi as ExtensionAPI, { judge: docsJudge });

    const cwd = join(import.meta.dir, "../../..");
    const mockContext: Partial<ExtensionContext> = {
      cwd,
      agent: { kind: "main", id: "1", name: "main", depth: 0 },
      ui: { notify: () => {} } as any,
    };

    // Route docs prompt -> zero skills selected
    await handlers.before_agent_start(
      {
        type: "before_agent_start",
        reason: "prompt",
        prompt: "Fix typo in documentation README",
        systemPrompt: [],
      },
      mockContext
    );

    // 1. Agent attempts to mutate strategy code with zero strategy skills -> must be blocked
    const mutateStrat = await handlers.tool_call(
      {
        type: "tool_call",
        toolCallId: "call_m1",
        toolName: "write",
        input: { path: "strategies/candidates/ts_momentum.py", content: "pass" },
      },
      mockContext
    );
    expect(mutateStrat).toBeDefined();
    expect(mutateStrat.block).toBe(true);
    expect(mutateStrat.reason).toContain("stale-scope gate");
    expect(mutateStrat.reason).toContain("zero loaded research skills");

    // 2. Agent attempts to run backtest with zero simulation skills -> must be blocked
    const runBt = await handlers.tool_call(
      {
        type: "tool_call",
        toolCallId: "call_bt1",
        toolName: "bash",
        input: { command: "uv run warden backtest experiments/active/hyp_001.yaml --json" },
      },
      mockContext
    );
    expect(runBt).toBeDefined();
    expect(runBt.block).toBe(true);
    expect(runBt.reason).toContain("stale-scope gate");
    expect(runBt.reason).toContain("zero loaded research skills");

    // 3. Regular non-strategy file edit (e.g. README.md) -> allowed
    const mutateDoc = await handlers.tool_call(
      {
        type: "tool_call",
        toolCallId: "call_doc",
        toolName: "write",
        input: { path: "README.md", content: "# Documentation" },
      },
      mockContext
    );
    expect(mutateDoc).toBeUndefined();
  });

  it("stale-scope gate allows operations when matching skills are present in coverage", async () => {
    const handlers: Record<string, Function> = {};
    const mockPi: Partial<ExtensionAPI> = {
      setLabel: () => {},
      on: (event: string, handler: Function) => {
        handlers[event] = handler;
      },
      appendEntry: () => {},
    };

    // Mock judge that selects feature and backtest skills
    const fullJudge = async (
      _st: RoutingState,
      questions: Record<string, Question>
    ): Promise<Answers> => {
      const ans: Answers = {};
      for (const k of Object.keys(questions)) {
        if (k === "ml4t-compute-features" || k === "ml4t-run-backtest") {
          ans[k] = { type: "bool", bool: 0.95 };
        } else {
          ans[k] = { type: "bool", bool: 0.05 };
        }
      }
      return ans;
    };

    skillRouterExtension(mockPi as ExtensionAPI, { judge: fullJudge });

    const cwd = join(import.meta.dir, "../../..");
    const mockContext: Partial<ExtensionContext> = {
      cwd,
      agent: { kind: "main", id: "1", name: "main", depth: 0 },
      ui: { notify: () => {} } as any,
    };

    await handlers.before_agent_start(
      {
        type: "before_agent_start",
        reason: "prompt",
        prompt: "Implement strategy and run backtest",
        systemPrompt: [],
      },
      mockContext
    );

    // Strategy mutation allowed because compute-features is in coverage
    const mutateStrat = await handlers.tool_call(
      {
        type: "tool_call",
        toolCallId: "call_m2",
        toolName: "write",
        input: { path: "strategies/candidates/ts_momentum.py", content: "pass" },
      },
      mockContext
    );
    expect(mutateStrat).toBeUndefined();

    // Backtest allowed because run-backtest is in coverage
    const runBt = await handlers.tool_call(
      {
        type: "tool_call",
        toolCallId: "call_bt2",
        toolName: "bash",
        input: { command: "uv run warden backtest experiments/active/hyp_001.yaml --json" },
      },
      mockContext
    );
    expect(runBt).toBeUndefined();
  });
});
