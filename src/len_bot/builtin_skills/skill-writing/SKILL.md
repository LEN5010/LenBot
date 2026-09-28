---
name: skill-writing
description: Write a focused, reusable task skill with valid metadata and instructions grounded in the task's available tools.
---

# Skill writing

Use this when the requested deliverable is a reusable skill. A task-created skill belongs in `/workspace/skills/<name>/SKILL.md`; it does not change another task or the host's approved shared skills automatically.

- Choose one narrow capability and a lowercase hyphenated directory name. Put the same `name` and a discriminating `description` in YAML frontmatter, then give the reader the useful workflow, inputs, outputs, and checks that are not obvious from the task alone.
- Describe only tools and services actually available to the intended environment. If a workflow needs a package, say how to add it explicitly in that task workspace; do not imply it was preinstalled. Avoid empty section templates, copied manuals, author credits, and unrequested invocation metadata.
- Re-read the file to check frontmatter, links, commands, and example paths. If the current session has not loaded the new skill, do not describe it as already active.
- For handoff, put the requested skill file or a Python-standard-library archive of its folder in `/workspace/out` and call `deliver_file(path, name, note)`. That only registers a copy; later adoption into group/shared skills is a separate operator action.
