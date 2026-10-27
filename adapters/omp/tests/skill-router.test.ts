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
});
