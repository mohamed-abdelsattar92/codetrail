// Commit message rules for codetrail (AGENTS.md, Workflow).
// Merge commits are ignored by commitlint's defaults. CI adds Dependabot's commits (commitlint.ci.config.mjs); the
// local commit-msg hook keeps these rules for every commit.
const TYPES = ["feat", "fix", "refactor", "perf", "test", "docs", "build", "ci", "chore", "revert"];
const SCOPES = [
  "config", "repo", "extract", "facts", "claude", "generate", "guide", "web", "bridge", "learn", "metrics",
  "tools", "docs", "adr", "ci", "deps",
];
const SECTIONS = ["What", "Why", "Alternatives considered", "Risks", "Agent and model"];

// Every non-merge commit carries its reasoning in a non-empty "Why" section.
function whySection(parsed) {
  const lines = (parsed.raw ?? "").split("\n").slice(1);
  const start = lines.findIndex((line) => line.trim() === "Why" || line.trim().startsWith("Why:"));
  if (start === -1) {
    return [false, 'the body needs a "Why" section (AGENTS.md, Workflow)'];
  }
  const inline = lines[start].trim().slice(4).trim();
  const section = inline ? [inline] : [];
  for (const line of lines.slice(start + 1)) {
    const trimmed = line.trim();
    if (SECTIONS.some((name) => trimmed === name || trimmed.startsWith(`${name}:`))) break;
    section.push(line);
  }
  return [section.some((line) => line.trim() !== "" && !line.startsWith("#")), 'the "Why" section is empty'];
}

export default {
  extends: ["@commitlint/config-conventional"],
  plugins: [{ rules: { "body-why-section": whySection } }],
  rules: {
    "type-enum": [2, "always", TYPES],
    "scope-enum": [2, "always", SCOPES],
    "scope-empty": [2, "never"],
    "body-why-section": [2, "always"],
    // Bodies hold paragraphs of prose; the What/Why sections set their own shape.
    "body-max-line-length": [0],
  },
};
