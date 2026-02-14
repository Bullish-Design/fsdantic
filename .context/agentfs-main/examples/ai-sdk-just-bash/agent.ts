/**
 * AI agent with persistent AgentFS storage
 *
 * This file contains only the agent logic - see shell.ts for the interactive loop.
 * Uses AgentFs to provide persistent filesystem storage backed by SQLite.
 *
 * The agent starts with the agentfs source code pre-loaded, so you can
 * explore the agentfs codebase using agentfs itself!
 */

import * as nodeFs from "node:fs";
import * as path from "node:path";
import { glob } from "glob";
import { anthropic } from "@ai-sdk/anthropic";
import { streamText, stepCountIs } from "ai";
import { createBashTool } from "bash-tool";
import { Bash } from "just-bash";
import { agentfs } from "agentfs-sdk/just-bash";

export interface AgentRunner {
  chat(
    message: string,
    callbacks: {
      onText: (text: string) => void;
    }
  ): Promise<void>;
}

export interface CreateAgentOptions {
  onToolCall?: (command: string) => void;
  onText?: (text: string) => void;
}

/**
 * Creates an agent runner with persistent filesystem storage
 *
 * Uses AgentFs backed by SQLite - files persist across sessions.
 */
export async function createAgent(
  options: CreateAgentOptions = {}
): Promise<AgentRunner> {
  // Open AgentFS for persistent storage
  const fs = await agentfs({ id: "just-bash-agent" });

  // Seed agentfs source files on first run
  const agentfsRoot = path.resolve(import.meta.dirname, "../..");
  if (!(await fs.exists("/README.md"))) {
    console.log("Seeding AgentFS with agentfs source files...");

    // Find all source files
    const patterns = [
      "**/*.ts",
      "**/*.rs",
      "**/*.toml",
      "**/*.json",
      "**/*.md",
    ];
    const ignorePatterns = [
      "**/node_modules/**",
      "**/dist/**",
      "**/target/**",
      "**/.git/**",
      "**/examples/just_bash/**",
    ];

    // Collect all files first
    const allFiles: string[] = [];
    for (const pattern of patterns) {
      const files = await glob(pattern, {
        cwd: agentfsRoot,
        ignore: ignorePatterns,
        nodir: true,
      });
      allFiles.push(...files);
    }

    // Copy files with progress
    let count = 0;
    const total = allFiles.length;
    for (const file of allFiles) {
      const srcPath = path.join(agentfsRoot, file);
      const destPath = "/" + file;

      // Create parent directories
      const dir = path.dirname(destPath);
      if (dir !== "/") {
        await fs.mkdir(dir, { recursive: true }).catch(() => {});
      }

      // Copy file
      const content = nodeFs.readFileSync(srcPath, "utf-8");
      await fs.writeFile(destPath, content);

      count++;
      if (count % 10 === 0 || count === total) {
        process.stdout.write(`\rSeeding: ${count}/${total} files...`);
      }
    }
    console.log("\nSeeded AgentFS with agentfs source files.");
  }

  // Create a just-bash Bash instance with AgentFS filesystem
  const bash = new Bash({ fs });

  // Create the bash toolkit with the just-bash sandbox
  const bashToolkit = await createBashTool({
    sandbox: bash,
    extraInstructions: `You are exploring the AgentFS codebase - a persistent filesystem for AI agents.
The filesystem is backed by AgentFS itself (SQLite), so files persist across sessions.

Use bash commands to explore:
- ls to see the project structure
- cat README.md to read documentation
- grep -r "pattern" . to search code
- find . -name "*.ts" to find TypeScript files

Key directories:
- /sdk/typescript/src - TypeScript SDK source
- /cli/src - CLI source (Rust)
- /integrations - Framework integrations`,
    onBeforeBashCall: options.onToolCall
      ? ({ command }) => {
          options.onToolCall!(command);
          return { command };
        }
      : undefined,
  });

  const history: Array<{ role: "user" | "assistant"; content: string }> = [];

  return {
    async chat(message, callbacks) {
      history.push({ role: "user", content: message });

      let fullText = "";

      const result = streamText({
        model: anthropic("claude-sonnet-4-20250514"),
        tools: bashToolkit.tools,
        stopWhen: stepCountIs(50),
        messages: history,
      });

      for await (const chunk of result.textStream) {
        options.onText?.(chunk);
        callbacks.onText(chunk);
        fullText += chunk;
      }

      history.push({ role: "assistant", content: fullText });
    },
  };
}
